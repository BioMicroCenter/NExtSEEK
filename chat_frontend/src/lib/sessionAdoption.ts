/**
 * Adopting the backend's session id after a terminal turn event.
 *
 * `useSessions` starts with `pendingNewChat === true`, and the only thing that
 * clears it is `promoteCreatedSession`. Until it is cleared, every send posts
 * `force_new: true` and the backend unconditionally creates a NEW session.
 *
 * That adoption used to live inline in the `query_complete` branch of both app
 * shells and nowhere else, so a turn that ended in `query_error` left
 * `pendingNewChat` true: the user typed again, the UI asked for another new
 * chat, and a second session appeared in the sidebar (#38). The error path has
 * exactly the same claim on the session as the success path — the backend
 * created it before the turn failed, and `query_error` carries the same
 * `session_id` (`assistant/pipeline_adapter.py` sets it for both terminal
 * events).
 *
 * Extracted here because the duplication IS the defect: two shells x two
 * terminal branches is four places to keep in step, and three of them were.
 */
export interface SessionAdopter {
  pendingNewChat: boolean;
  promoteCreatedSession: (id: string) => void;
  refresh: () => void | Promise<void>;
}

export function adoptTerminalSession(
  sessions: SessionAdopter,
  sessionId: string | null | undefined,
): void {
  if (!sessionId) return;
  if (sessions.pendingNewChat) sessions.promoteCreatedSession(sessionId);
  else void sessions.refresh();
}
