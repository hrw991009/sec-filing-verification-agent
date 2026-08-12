export interface AccessSession {
  readonly expiresAt: string;
  readonly token: string;
}

type SessionListener = () => void;

let currentSession: AccessSession | null = null;
let sessionRevision = 0;
const listeners = new Set<SessionListener>();

export function getAccessSession(): AccessSession | null {
  return currentSession;
}

export function getAccessSessionRevision(): number {
  return sessionRevision;
}

export function setAccessSession(session: AccessSession, expectedRevision?: number): boolean {
  if (expectedRevision !== undefined && expectedRevision !== sessionRevision) {
    return false;
  }

  currentSession = session;
  sessionRevision += 1;
  listeners.forEach((listener) => {
    listener();
  });
  return true;
}

export function clearAccessSession(expectedRevision?: number): boolean {
  if (expectedRevision !== undefined && expectedRevision !== sessionRevision) {
    return false;
  }

  currentSession = null;
  sessionRevision += 1;
  listeners.forEach((listener) => {
    listener();
  });
  return true;
}

export function subscribeToAccessSession(listener: SessionListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
