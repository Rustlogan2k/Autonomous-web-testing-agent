// The declaration of the result, made once when the returning officer publishes it.

/**
 * The declaration for a finished count, or null when the count elected nobody (a tie
 * cannot be declared). It names the candidate the count elected, that candidate's votes
 * in the final round and the continuing ballots in that round.
 */
export function buildDeclaration(count, candidates) {
  if (!count || count.result.kind !== "elected") return null;
  const elected = count.rounds[count.rounds.length - 1].leader;
  const finalRound = count.rounds.at(-1);
  const candidate = candidates.find((entry) => entry.id === elected);
  return {
    candidate: elected,
    name: candidate ? candidate.name : elected,
    votes: finalRound.tallies[elected] ?? 0,
    continuing: finalRound.continuing,
    exhausted: finalRound.exhausted,
    round: finalRound.number,
    ballots: finalRound.continuing + finalRound.exhausted,
  };
}
