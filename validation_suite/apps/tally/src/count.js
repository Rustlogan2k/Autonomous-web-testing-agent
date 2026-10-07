// The instant-runoff count, as the club's rules of the count describe it (see /help).
//
// Each round, every ballot counts for its highest-ranked candidate still in the count; a
// ballot with no such candidate left is exhausted. A candidate with more than half of the
// continuing ballots is elected. Otherwise the candidate with the fewest votes is eliminated
// (ties: fewer first preferences, then the one later on the paper) and the next round
// begins. When two candidates are left level, the count ends Tied and nobody is elected.

/**
 * Count `ballots` ({paper, preferences}) for `candidates` (in ballot-paper order).
 * Returns {rounds, result}; each round is {number, tallies, continuing, exhausted, leader,
 * leaderVotes, outcome}, where outcome is {kind: "elected" | "eliminated" | "tied", candidate}.
 */
export function countBallots(ballots, candidates) {
  const order = candidates.map((candidate) => candidate.id);
  const firstPreferences = Object.fromEntries(order.map((id) => [id, 0]));
  for (const ballot of ballots) {
    if (ballot.preferences.length) firstPreferences[ballot.preferences[0]] += 1;
  }
  let standing = [...order];
  const rounds = [];
  for (;;) {
    const tallies = Object.fromEntries(standing.map((id) => [id, 0]));
    let exhausted = 0;
    for (const ballot of ballots) {
      const choice = ballot.preferences.find((id) => standing.includes(id));
      if (choice) tallies[choice] += 1;
      else exhausted += 1;
    }
    const continuing = ballots.length - exhausted;
    const byVotes = [...standing].sort((a, b) => tallies[b] - tallies[a] || order.indexOf(a) - order.indexOf(b));
    const top = { candidate: byVotes[0], votes: tallies[byVotes[0]] };
    const round = {
      number: rounds.length + 1, tallies, continuing, exhausted,
      leader: top.candidate, leaderVotes: top.votes, outcome: null,
    };
    rounds.push(round);
    if (top.votes * 2 > continuing) {
      round.outcome = { kind: "elected", candidate: top.candidate };
      return { rounds, result: round.outcome };
    }
    if (standing.length <= 2) {
      round.outcome = { kind: "tied", candidate: null };
      return { rounds, result: round.outcome };
    }
    // Fewest votes goes out; ties: fewer first preferences, then later on the paper.
    const out = [...standing].sort((a, b) => tallies[a] - tallies[b]
      || firstPreferences[a] - firstPreferences[b]
      || order.indexOf(b) - order.indexOf(a))[0];
    round.outcome = { kind: "eliminated", candidate: out };
    standing = standing.filter((id) => id !== out);
  }
}
