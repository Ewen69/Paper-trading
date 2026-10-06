/** Fired after anything that changes game state (a backtest run, an import) to refresh XP. */
export const ACTIVITY_EVENT = 'ptl:activity';

export const announceActivity = () => window.dispatchEvent(new Event(ACTIVITY_EVENT));
