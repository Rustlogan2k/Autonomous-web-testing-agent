"""Gymnasium base class shared by WebFunctionalEnv (Agent B) and, later, WebSecurityEnv (Agent A).

Owns the browser lifecycle, raw multimodal observation capture, and the
reset/step orchestration described in the spec. Subclasses only need to supply
the page-type-specific pieces: how the dynamic action space is built, how a
chosen action is executed, and how the reward for a transition is computed.
"""

from __future__ import annotations

import io
import json
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, replace
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces
from PIL import Image

from ..perception.normalization import state_fingerprint
from ..reward.exploration import ExplorationSignal, ExplorationTracker
from ..reward.functional_triggers import ErrorBaseline, FindingLedger
from ..utils.logging import get_logger
from .browser_session import BrowserSession
from .network_recorder import NetworkRecorder
from .types import CANONICAL_VIEWPORT, MAX_ACTIONS, SCREENSHOT_SHAPE, ActionSpec, ActionType, RawObservation

logger = get_logger(__name__)

_HTML_TEXT_MAX = 200_000
_NETWORK_TEXT_MAX = 100_000

# `page.content()` serializes DOM *attributes*, but user input lives in *properties*:
# typing sets `input.value` while the `value` attribute stays as authored, and checking
# a box sets `input.checked` with no attribute change at all. The captured HTML was
# therefore byte-identical before and after every TYPE and every checkbox toggle.
#
# Three consequences, all measured on the toy site: BUG-09 (a preference that fails to
# persist) was completely invisible in the observation; `state_fingerprint` treated an
# empty and a fully-filled form as the same state, so making progress through a flow
# earned no novelty reward and `unique_states` undercounted; and the structural encoder
# could not distinguish them either.
#
# The same pass marks elements that are present but not rendered (`data-hidden`), which
# is the only way a text view can see a responsive-layout regression.
#
# The projection runs against a *clone*, never the live DOM: writing these attributes
# back into the real document would fire the application's own MutationObservers and
# perturb the behaviour under test.
_SERIALIZE_JS = """
() => {
    const SEL = 'a, button, input, textarea, select, option';
    const clone = document.documentElement.cloneNode(true);
    const live = document.querySelectorAll(SEL);
    const copy = clone.querySelectorAll(SEL);
    const n = Math.min(live.length, copy.length);
    for (let i = 0; i < n; i++) {
        const source = live[i], target = copy[i];
        const tag = source.tagName;
        if (tag !== 'OPTION') {
            // Responsive-layout bugs are invisible to any text view of the markup:
            // an element hidden by a `max-width` media query is still fully present
            // in the DOM. Only *hidden* elements are marked, so the annotation stays
            // sparse on a normal page.
            const style = window.getComputedStyle(source);
            const rect = source.getBoundingClientRect();
            if (style.display === 'none' || style.visibility === 'hidden'
                || rect.width === 0 || rect.height === 0) {
                target.setAttribute('data-hidden', 'true');
            }
        }
        if (tag === 'INPUT') {
            const type = (source.getAttribute('type') || 'text').toLowerCase();
            if (type === 'checkbox' || type === 'radio') {
                if (source.checked) target.setAttribute('checked', '');
                else target.removeAttribute('checked');
            } else if (type === 'password') {
                // The value is deliberately never projected: the observation is written
                // to disk and passed to an LLM judge. But *withholding it silently* is
                // its own defect — the markup then stays byte-identical across every
                // TYPE into a password field, and the window reports "NO OBSERVABLE
                // CHANGE", which is the strongest bug signal in the prompt. Measured on
                // Gitea's login form: two confident verdicts (`ui_regression`, `other`)
                // on an application that had behaved perfectly.
                //
                // A fixed marker restores *occupancy* — empty vs. filled, which is what
                // state identity and the judge both actually need — while leaking
                // neither the content nor its length.
                target.setAttribute('value', source.value ? '[password withheld]' : '');
            } else {
                target.setAttribute('value', source.value);
            }
        } else if (tag === 'TEXTAREA') {
            target.textContent = source.value;
        } else if (tag === 'OPTION') {
            if (source.selected) target.setAttribute('selected', '');
            else target.removeAttribute('selected');
        }
    }
    return '<!DOCTYPE html>' + clone.outerHTML;
}
"""


@dataclass(slots=True)
class StepContext:
    """Everything a subclass needs to score one transition.

    Passed as one object rather than eight positional arguments so that adding a new
    signal (as `settled` and `opened_new_page` were added) does not break every
    subclass's `_compute_reward` signature.
    """

    pre_obs: RawObservation
    spec: ActionSpec
    post_obs: RawObservation
    exec_info: dict
    load_duration_s: float
    settled: bool
    previously_settled: bool
    opened_new_page: bool
    exploration: ExplorationSignal | None
    error_baseline: ErrorBaseline
    # Fingerprint of the page state reached by this step; identifies a finding's location.
    state_key: str
    finding_ledger: FindingLedger


class WebTestingEnv(gym.Env, ABC):
    """Playwright-backed Gymnasium environment over one target web application."""

    metadata: dict[str, Any] = {"render_modes": ["rgb_array"], "render_fps": 1}

    def __init__(
        self,
        base_url: str,
        max_steps: int = 200,
        headless: bool = True,
        render_mode: str | None = None,
        repetition_window: int = 20,
        setup_actions: list[dict] | None = None,
    ) -> None:
        super().__init__()
        if render_mode is not None and render_mode not in self.metadata["render_modes"]:
            raise ValueError(f"Unsupported render_mode: {render_mode!r}")

        self.base_url = base_url
        self.max_steps = max_steps
        self.render_mode = render_mode
        # Replayed at the start of every episode, before the first observation. Same
        # step schema as a repro script; see `_run_setup_actions`.
        self.setup_actions = list(setup_actions or [])
        self._bootstrap_steps = 0
        self._base_domain = BrowserSession.domain_of(base_url)

        self._browser = BrowserSession(headless=headless)
        self._network_recorder: NetworkRecorder | None = None
        self._carried_network_events: list = []
        self._console_buffer: list[str] = []
        self._page_error_buffer: list[str] = []

        self._action_specs: list[ActionSpec] = []
        self._last_raw_obs: RawObservation | None = None
        self._steps_taken = 0
        self._episode_id = 0
        # Whether the page was quiescent at the end of the previous step. `slow_response`
        # is edge-triggered on this so a permanently-churning page is reported once,
        # against the action that caused it, not against every action after it.
        self._page_settled = True

        self.error_baseline = ErrorBaseline()
        self.exploration = ExplorationTracker(repetition_window=repetition_window)
        self.finding_ledger = FindingLedger()

        self.action_space = spaces.Discrete(MAX_ACTIONS)
        # Only the screenshot is a well-formed Gym space. Page HTML, the network trace
        # and the URL are arbitrary Unicode and cannot be honestly expressed as a
        # `spaces.Text` (its charset is a finite character set — the default is 62
        # alphanumerics, so `Text.contains("<html>")` is False and `check_env` fails).
        # They travel in `info["page"]` instead, which is Gymnasium's channel for
        # auxiliary per-step data, and the perception layer reads them from there.
        self.observation_space = spaces.Dict(
            {"screenshot": spaces.Box(low=0, high=255, shape=SCREENSHOT_SHAPE, dtype=np.uint8)}
        )

    # -- subclass responsibilities -------------------------------------------------

    @abstractmethod
    def _build_action_specs(self) -> list[ActionSpec]:
        """Rebuild the dynamic action list for the page currently loaded in `self._browser.page`."""

    @abstractmethod
    def _execute_action(self, spec: ActionSpec) -> dict:
        """Perform `spec` against `self._browser.page`. Return execution metadata for reward/info."""

    @abstractmethod
    def _compute_reward(self, context: StepContext) -> tuple[float, dict]:
        """Return (reward, info) for the transition described by `context`."""

    # -- Gymnasium API ---------------------------------------------------------

    def reset(
        self, *, seed: int | None = None, options: dict | None = None
    ) -> tuple[dict, dict]:
        super().reset(seed=seed)
        self._episode_id += 1
        self._steps_taken = 0
        self._console_buffer = []
        self._page_error_buffer = []
        self._carried_network_events = []

        logger.info("[episode {}] resetting to {}", self._episode_id, self.base_url)
        page = self._browser.new_episode(self.base_url)
        self._attach_page_listeners(page)
        # A landing page that never settles is the app's own baseline, not a discovery.
        self._page_settled = self._browser.wait_settled()

        # Session bootstrap runs here: after the landing page, before the first
        # observation is captured. Its steps are deliberately *not* counted against
        # max_steps and never reach the reward — logging in is test setup, not a
        # discovery, and paying an agent for it would make "log in again" a reward
        # source. Every episode starts from a fresh context, so without this the agent
        # spends its whole budget re-finding the login form on any app whose interesting
        # surface is behind one.
        self._bootstrap_steps = self._run_setup_actions()

        self._last_raw_obs = self._capture_raw_observation()
        # Errors seen while merely loading the landing page are this app's baseline
        # noise (favicons, sourcemaps, optional bundles), not bugs the agent found.
        self.error_baseline.learn(self._last_raw_obs.network_events)
        self._action_specs = self._build_action_specs()

        self.exploration.start_episode()
        self.finding_ledger.start_episode()
        # Prime the reveal baseline with the landing page's own action set, so the first
        # step is diffed against something rather than against nothing.
        self.exploration.observe_action_set(self._action_specs, self._last_raw_obs.url)
        # A judge keeps its own rolling window for cross-step evidence. A fresh browser
        # context shares no state with the previous episode, so carrying records across
        # the boundary would invite causal links that cannot exist.
        self._start_reward_model_episode()
        self.exploration.observe_reset(self._state_key(self._last_raw_obs))

        info = {
            "episode_id": self._episode_id,
            "action_specs": self._action_specs,
            "page": self._page_info(self._last_raw_obs),
            "state_key": self._state_key(self._last_raw_obs),
            "episode_context": self._episode_context(self._state_key(self._last_raw_obs), None),
            "baseline_errors": len(self.error_baseline),
            # Present on reset as well as step. A masked policy reads this on every
            # observation, and the first observation of an episode is no exception —
            # omitting it here would leave step 0 masked against a stale or empty count.
            "num_valid_actions": len(self._action_specs),
            "newly_revealed": [],
            "state_visits": self.exploration.state_visits(self._state_key(self._last_raw_obs)),
            # Recorded so a corpus states whether its episodes began authenticated. A
            # trace that silently differs in session state is not comparable with one
            # that does not, and the difference is invisible in the pages themselves.
            "bootstrap_steps": self._bootstrap_steps,
            "authenticated": bool(self.setup_actions) and self._bootstrap_steps > 0,
        }
        return self._to_gym_obs(self._last_raw_obs), info

    def step(self, action: int) -> tuple[dict, float, bool, bool, dict]:
        if self._last_raw_obs is None:
            raise RuntimeError("step() called before reset()")

        spec = self._resolve_action(action)
        pre_obs = self._last_raw_obs

        start = time.monotonic()
        # Which tabs already existed, so a popup this action opens can be told apart
        # from one an earlier action opened that Playwright is only now reporting.
        self._browser.mark_pages()
        try:
            exec_info = self._execute_action(spec)
        except Exception as exc:  # noqa: BLE001 - a failed action is data, not a crash
            logger.warning("[episode {}] action {} raised: {}", self._episode_id, spec.description, exc)
            exec_info = {"success": False, "error": str(exc)}

        # Settle first, then look for a popup: Playwright's sync API only dispatches the
        # page-created event when it next yields, so checking before this wait always
        # misses a popup the action just opened.
        settled = self._browser.wait_settled()
        opened_new_page, offsite_tab = self._adopt_new_page_if_any()
        if opened_new_page:
            settled = self._browser.wait_settled()
        if offsite_tab:
            # The click opened a tab pointing outside the application. Nothing about the
            # application changed and nothing was refused — the tab was simply not
            # followed. Recorded distinctly so the window can say exactly that instead
            # of reporting either a dead control or a blocked navigation.
            exec_info = {**exec_info, "opened_offsite_tab": offsite_tab}

        # Leaving the application is refused and undone, not treated as the end of the
        # episode. Terminating instead was measured against Gitea on first contact:
        # its footer links point at github.com and docs.gitea.com, the explorer reached
        # one within a few steps, and three 40-step episodes yielded 22 steps in total.
        # It is also the same shape as reward-hacking exploit #4 (§5) — an early exit is
        # worth a lot to an agent accumulating negative reward, and on a real
        # application an off-site link is always within reach.
        if self._left_target_domain(self._browser.page.url if self._browser.page else ""):
            left_for = self._browser.page.url if self._browser.page else ""
            restored = self._browser.restore(pre_obs.url)
            exec_info = {
                **exec_info,
                "success": False,
                "error": f"blocked: navigation left the application ({left_for})",
                "left_application": left_for,
            }
            settled = self._browser.wait_settled() if restored else settled
            # The off-site page loads far enough to run its own scripts before the
            # restore completes, and everything it logs lands in these buffers. Left
            # in place, third-party analytics and CSP violations from someone else's
            # site are reported as defects in the application under test — measured on
            # Gitea, where `cloudflareinsights.com` CSP errors and a Docusaurus React
            # warning from its documentation site produced 5 confident js_error
            # verdicts. Nothing observed off-site is evidence about this application.
            self._console_buffer = []
            self._page_error_buffer = []
            if self._network_recorder is not None:
                self._network_recorder.drain()
            self._carried_network_events = []
        load_duration_s = time.monotonic() - start
        previously_settled, self._page_settled = self._page_settled, settled

        post_obs = self._capture_raw_observation()
        self._action_specs = self._build_action_specs()
        self._last_raw_obs = post_obs
        self._steps_taken += 1

        state_key = self._state_key(post_obs)
        # Diffed against the action set that was available *before* this step. A gated
        # flow reveals its next control once its input is valid, so this is a direct,
        # answer-key-free progress signal. Computed here, once, and consumed by both the
        # reward and the policy's action features (via `info`) — a second definition
        # outside the env would be free to drift from this one, which is exactly how
        # `state_key` came to be exposed rather than recomputed.
        newly_revealed = self.exploration.observe_action_set(self._action_specs, post_obs.url)
        exploration = self.exploration.observe_step(spec, state_key, newly_revealed, post_obs.url)

        context = StepContext(
            pre_obs=pre_obs,
            spec=spec,
            post_obs=post_obs,
            exec_info=exec_info,
            load_duration_s=load_duration_s,
            settled=settled,
            previously_settled=previously_settled,
            opened_new_page=opened_new_page,
            exploration=exploration,
            error_baseline=self.error_baseline,
            state_key=state_key,
            finding_ledger=self.finding_ledger,
        )
        reward, reward_info = self._compute_reward(context)

        # Off-site navigation is refused and undone above, so reaching here means the
        # restore itself failed and the browser is genuinely stranded outside the
        # application. Only then is ending the episode the right call.
        terminated = self._left_target_domain(post_obs.url)
        truncated = self._steps_taken >= self.max_steps
        if terminated:
            logger.warning(
                "[episode {}] terminated: could not return to the application after "
                "navigating to {} (target domain {})",
                self._episode_id, post_obs.url[:120], self._base_domain,
            )

        info = {
            "step": self._steps_taken,
            "action_spec": spec,
            # The coarse state identity the exploration bonus is keyed on. Exposed
            # because an archive-based explorer needs the same notion of "somewhere I
            # have already been" the reward uses; recomputing it outside the env would
            # be a second definition free to drift from this one.
            "state_key": state_key,
            "exec_info": exec_info,
            "network_settled": settled,
            "opened_new_page": opened_new_page,
            "load_duration_s": load_duration_s,
            "action_specs": self._action_specs,
            "num_valid_actions": len(self._action_specs),
            # Action identities that appeared as a result of this step. The policy's
            # action features read this rather than recomputing the diff.
            "newly_revealed": sorted(newly_revealed),
            "state_visits": self.exploration.state_visits(state_key),
            "page": self._page_info(post_obs),
            "episode_context": self._episode_context(state_key, exploration),
            **reward_info,
        }
        return self._to_gym_obs(post_obs), reward, terminated, truncated, info

    def render(self) -> np.ndarray | None:
        if self.render_mode != "rgb_array" or self._last_raw_obs is None:
            return None
        return self._last_raw_obs.screenshot

    def close(self) -> None:
        self._browser.close()

    # -- internals ---------------------------------------------------------

    def _attach_page_listeners(self, page) -> None:  # noqa: ANN001 - playwright.sync_api.Page
        page.on("console", self._on_console_message)
        page.on("pageerror", self._on_page_error)
        self._network_recorder = NetworkRecorder(page)

    def _adopt_new_page_if_any(self) -> tuple[bool, str]:
        """Follow an in-app tab the action opened; discard an off-site one.

        Returns `(adopted, offsite_url)`. An off-site tab is closed rather than adopted:
        the page the click came from never navigated, so adopting it only to refuse and
        "restore" reports a navigation that did not happen — and the restore's reload
        wipes any typed form state, which then reads as the application discarding user
        input.
        """
        adopted, offsite = self._browser.take_new_page(
            accept=lambda url: not self._left_target_domain(url)
        )
        if adopted is None:
            return False, offsite
        # Don't lose what the old page recorded before it was superseded.
        if self._network_recorder is not None:
            self._carried_network_events.extend(self._network_recorder.drain())
        self._attach_page_listeners(adopted)
        return True, ""

    def _run_setup_actions(self) -> int:
        """Execute the session-bootstrap macro, returning how many steps landed.

        Steps use the same schema as a repro script (`{"type": ..., "id"/"text": ...,
        "params": {...}}`) and are resolved against the live action space by the same
        matcher, so a login macro is written exactly like a repro path and needs no
        second selector language.

        A failed bootstrap is logged loudly rather than raised. An episode that silently
        started anonymous when it was meant to be authenticated produces a corpus in
        which every authenticated finding is missing, and that is indistinguishable
        downstream from a judge that found nothing.
        """
        if not self.setup_actions:
            return 0
        from ..evaluation.scripted import ScriptedPolicy  # local: evaluation imports envs

        completed = 0
        for position, step in enumerate(self.setup_actions):
            specs = self._build_action_specs()
            # A literal value is pulled out before matching and applied after. The action
            # registry only ever generates TYPE actions with *generated* values (one per
            # value category), because that is what an explorer needs — but a login macro
            # needs one exact string, and no generated action will ever equal it. Kept
            # here rather than in the registry so the agent's action space is unchanged:
            # a slot that types a real password is not something the policy should be
            # able to choose.
            literal = (step.get("params") or {}).get("value")
            match_step = step
            if literal is not None:
                params = {k: v for k, v in step["params"].items() if k not in ("value", "category")}
                match_step = {**step, "params": params}
            index = ScriptedPolicy._match(match_step, specs)
            if index is None:
                logger.warning(
                    "[episode {}] session bootstrap step {} {} did not match any action on {}; "
                    "the episode will run unauthenticated",
                    self._episode_id, position, step, self._browser.page.url if self._browser.page else "?",
                )
                continue
            spec = specs[index]
            if literal is not None:
                spec = replace(spec, params={**spec.params, "value": literal})
            try:
                self._execute_action(spec)
            except Exception as exc:  # noqa: BLE001 - setup failure must not crash the run
                logger.warning("[episode {}] bootstrap step {} raised: {}", self._episode_id, position, exc)
                continue
            self._browser.wait_settled()
            completed += 1

        self._page_settled = self._browser.wait_settled()
        # Anything the login flow logged or requested is setup noise, not a finding.
        self._console_buffer = []
        self._page_error_buffer = []
        if self._network_recorder is not None:
            self._network_recorder.drain()
        self._carried_network_events = []
        logger.info(
            "[episode {}] session bootstrap: {}/{} steps -> {}",
            self._episode_id, completed, len(self.setup_actions),
            self._browser.page.url if self._browser.page else "?",
        )
        return completed

    def _start_reward_model_episode(self) -> None:
        """Signal a new episode to whatever judge the subclass owns.

        Declared here because `reset()` is the only place that knows an episode has
        begun, but the reward model belongs to the subclass — `WebTestingEnv` itself has
        none. Default no-op rather than a `getattr` probe, so a subclass that acquires a
        reward model has an explicit place to say so.
        """

    def _resolve_action(self, action: int) -> ActionSpec:
        if 0 <= action < len(self._action_specs):
            return self._action_specs[action]
        logger.debug("Action index {} out of range for {} specs; treating as NO_OP", action, len(self._action_specs))
        return ActionSpec(index=action, action_type=ActionType.NO_OP, description="no-op (out of range)")

    def _left_target_domain(self, url: str) -> bool:
        """Whether the agent genuinely navigated out of the application under test.

        A non-http(s) URL (`about:blank` after a history escape, `blob:`/`data:` after a
        download, browser-internal pages) is *not* a navigation away — it is an artifact.
        Treating it as one ended episodes after a single step and handed the agent a way
        to opt out of the whole task, which a DQN duly learned to exploit.
        """
        if not url.lower().startswith(("http://", "https://")):
            logger.debug("Non-http URL {!r}; not treating as leaving the target domain", url[:80])
            return False
        return BrowserSession.domain_of(url) != self._base_domain

    @staticmethod
    def _state_key(obs: RawObservation) -> str:
        return state_fingerprint(obs.url, obs.html)

    def _episode_context(self, state_key: str, exploration: ExplorationSignal | None) -> np.ndarray:
        """Compact summary of episode history, appended to the agent's observation.

        Reward here is history-dependent by design: the novelty bonus pays once per new
        state, and `FindingLedger` pays each finding once per episode. Without these
        features the observation cannot distinguish "first visit, bug worth +7" from
        "fifth visit, same bug worth 0", so the Q-target is unpredictable from the state
        and training is unstable — the peak-then-collapse curve measured on the toy site.

        All entries are normalized to roughly [0, 1] so no single term dominates the
        input scale of a network whose other 1664 dims are unit-norm embeddings.
        """
        window = max(self.exploration.repetition_window, 1)
        return np.array(
            [
                min(self._steps_taken / max(self.max_steps, 1), 1.0),
                min(self.exploration.episode_states_seen / 32.0, 1.0),
                1.0 if (exploration is not None and exploration.is_novel_state) else 0.0,
                min((exploration.repeat_count if exploration else 0) / window, 1.0),
                min(self.finding_ledger.paid_at(state_key) / 8.0, 1.0),
                min(len(self.finding_ledger) / 16.0, 1.0),
            ],
            dtype=np.float32,
        )

    def _is_foreign_source(self, source_url: str) -> bool:
        """Whether a console/page event originated outside the application under test.

        Clearing the buffers when an off-site navigation is refused (see `step`) handles
        everything the foreign page had already logged, but it is a *timing* fix and the
        race it loses is routine: a third-party beacon requested by the off-site document
        resolves a moment later, lands in the buffer after the clear, and is attributed to
        whichever step of the application happens to be running. Measured on the recapture
        that was supposed to prove the clear sufficient — two `static.cloudflareinsights.com`
        errors from a `code.gitea.io` page arrived one step after the refusal.

        Keying on provenance instead of arrival time closes the race outright: an event
        that names a foreign origin is not evidence about this application whenever it
        shows up. Events with no usable source URL fall back to the page they arrived on,
        which is the same judgement the clear was making.

        Deliberate trade-off: a script served from a third-party host *onto the
        application's own page* (a CDN-hosted library) is judged by its own origin and
        dropped. That loses a genuine-but-rare signal to remove a measured false-positive
        class, and a defect inside someone else's bundle is a weak finding about this app.
        """
        candidate = source_url or (self._browser.page.url if self._browser.page else "")
        if not candidate.lower().startswith(("http://", "https://")):
            # data:/blob:/about: sources are generated by the page itself, not fetched
            # from anywhere, so there is no foreign origin to attribute them to.
            return False
        return BrowserSession.domain_of(candidate) != self._base_domain

    @staticmethod
    def _console_source(message) -> str:  # noqa: ANN001 - playwright.sync_api.ConsoleMessage
        try:
            return (message.location or {}).get("url") or ""
        except Exception:  # noqa: BLE001 - location is best-effort metadata, never worth a crash
            return ""

    def _on_console_message(self, message) -> None:  # noqa: ANN001 - playwright.sync_api.ConsoleMessage
        if message.type != "error":
            return
        source = self._console_source(message)
        if self._is_foreign_source(source):
            logger.debug("Dropping console error from foreign origin {}: {}", source[:80], message.text[:80])
            return
        self._console_buffer.append(message.text)

    def _on_page_error(self, error) -> None:  # noqa: ANN001 - playwright.sync_api.Error
        # An uncaught exception carries no location, so it is attributed to the document
        # it was raised in — which is what `_is_foreign_source` falls back to.
        if self._is_foreign_source(""):
            return
        self._page_error_buffer.append(str(error))

    def _capture_raw_observation(self) -> RawObservation:
        page = self._browser.page
        assert page is not None
        assert self._network_recorder is not None

        try:
            screenshot = self._canonicalize_screenshot(page.screenshot(type="png"))
        except Exception as exc:  # noqa: BLE001 - page may be mid-navigation
            logger.warning("Screenshot capture failed: {}", exc)
            screenshot = np.zeros(SCREENSHOT_SHAPE, dtype=np.uint8)

        html = self._capture_html(page)

        network_events = self._carried_network_events + self._network_recorder.drain()
        self._carried_network_events = []
        console_errors, self._console_buffer = self._console_buffer, []
        page_errors, self._page_error_buffer = self._page_error_buffer, []

        return RawObservation(
            screenshot=screenshot,
            html=html,
            network_events=network_events,
            url=page.url,
            console_errors=console_errors,
            page_errors=page_errors,
        )

    @staticmethod
    def _capture_html(page) -> str:  # noqa: ANN001 - playwright.sync_api.Page
        """Serialize the page with live form state projected into the markup.

        Falls back to plain `page.content()` if the projection fails — a mid-navigation
        capture loses its execution context, and a degraded observation is better than
        a dead episode. See `_SERIALIZE_JS` for why the projection is needed at all.
        """
        try:
            return page.evaluate(_SERIALIZE_JS)
        except Exception as exc:  # noqa: BLE001 - navigation destroyed the context
            logger.debug("Form-state projection failed, falling back to content(): {}", exc)
        try:
            return page.content()
        except Exception as exc:  # noqa: BLE001
            logger.warning("HTML capture failed: {}", exc)
            return ""

    @staticmethod
    def _canonicalize_screenshot(png_bytes: bytes) -> np.ndarray:
        """Decode a screenshot and force it to SCREENSHOT_SHAPE.

        RESIZE_VIEWPORT deliberately changes the real viewport — that is the whole
        point of the action, and responsive-layout bugs only appear at 375px wide. But
        the *tensor* must keep one shape or every post-resize observation violates the
        declared observation space, so the capture is rescaled back here.
        """
        image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        target = (CANONICAL_VIEWPORT["width"], CANONICAL_VIEWPORT["height"])
        if image.size != target:
            image = image.resize(target, Image.BILINEAR)
        return np.asarray(image, dtype=np.uint8)

    @staticmethod
    def _serialize_network(events: list) -> tuple[str, bool]:
        """Serialize the network trace to a JSON *list* that fits the byte budget.

        The previous implementation serialized every event and sliced the resulting
        string at the budget. That cuts mid-object and yields invalid JSON, so every
        consumer that parses it loses the **entire** trace for that step — not the tail.
        The toy site's payloads are orders of magnitude too small to reach the budget,
        so it never fired there; a real application's would, and would do so on exactly
        the busiest steps, silently removing all HTTP evidence precisely where a failed
        request is most likely.

        When the budget binds, errors and document requests are kept in preference to
        successful subresources: a dropped image is noise, a dropped 500 is the finding.
        Returns the JSON and whether anything had to be dropped.
        """
        rows = [(event, asdict(event)) for event in events]
        payload = json.dumps([row for _event, row in rows], default=str)
        if len(payload) <= _NETWORK_TEXT_MAX:
            return payload, False

        # Stable partition: important events first, each group keeping arrival order.
        important = [row for event, row in rows if event.is_error or event.is_document]
        rest = [row for event, row in rows if not (event.is_error or event.is_document)]

        # Sized incrementally rather than by re-serializing the accumulated list each
        # time: a busy page can produce hundreds of events, and the quadratic form of
        # this loop is a real cost on the very steps that trigger it. One oversized
        # event is skipped rather than ending the loop, so a single large response body
        # cannot shut out every event behind it.
        kept: list[dict] = []
        size = 2  # the enclosing "[]"
        for row in important + rest:
            row_size = len(json.dumps(row, default=str)) + (1 if kept else 0)  # + comma
            if size + row_size > _NETWORK_TEXT_MAX:
                continue
            kept.append(row)
            size += row_size
        logger.debug(
            "Network trace over budget: kept {} of {} events ({} errors/documents available)",
            len(kept), len(rows), len(important),
        )
        return json.dumps(kept, default=str), True

    @staticmethod
    def _page_info(raw: RawObservation) -> dict:
        """The textual half of the observation, carried in `info` (see observation_space)."""
        network_json, truncated = WebTestingEnv._serialize_network(raw.network_events)
        return {
            "url": raw.url,
            "html": raw.html[:_HTML_TEXT_MAX],
            "network": network_json,
            "network_truncated": truncated,
            "console_errors": list(raw.console_errors),
            "page_errors": list(raw.page_errors),
        }

    @staticmethod
    def _to_gym_obs(raw: RawObservation) -> dict:
        return {"screenshot": raw.screenshot}
