import type {
  Book,
  ChatSummary,
  ChatTurn,
  IngestionProgress,
  Message,
  Session,
  StoryGraph,
  Reasoning,
  Storyline,
  TraceStep,
  User,
  Visibility,
} from "./types";
import { mockApi } from "./mock/server";
import { httpApi } from "./http";

/** Streamed while a chat turn is being produced. */
export type ChatEvent =
  | { type: "phase"; label: string }
  | { type: "step"; step: TraceStep }
  | { type: "reasoning"; reasoning: Reasoning }
  | { type: "token"; text: string };

export interface Api {
  // auth
  signUp(name: string, email: string, password: string): Promise<Session>;
  signIn(email: string, password: string): Promise<Session>;
  /** Trades the refresh cookie for a new session; rejects when there is no valid one. */
  refresh(): Promise<Session>;
  /** Ends the session on the server. */
  signOut(): Promise<void>;
  me(): Promise<User>;
  /** Saves the user's Inkitt access token after Inkitt accepts it. */
  setInkittToken(token: string): Promise<User>;
  clearInkittToken(): Promise<User>;

  // books
  listPublicBooks(): Promise<Book[]>;
  listMyBooks(): Promise<Book[]>;
  getBook(id: string): Promise<Book>;
  uploadBook(file: File, meta: { title: string; author: string; visibility: Visibility }): Promise<Book>;
  /** Calls `onProgress` until the book is ready or failed. Returns an unsubscribe. */
  watchIngestion(bookId: string, onProgress: (p: IngestionProgress) => void): () => void;

  // graph
  getGraph(bookId: string, storylineId: string | null): Promise<StoryGraph>;

  // storylines
  listStorylines(bookId: string): Promise<Storyline[]>;
  listPublicStorylines(): Promise<Storyline[]>;
  listMyStorylines(): Promise<Storyline[]>;
  getStoryline(id: string): Promise<Storyline>;
  createStoryline(bookId: string, title: string, premise: string): Promise<Storyline>;
  updateStoryline(id: string, patch: { title?: string; visibility?: Visibility }): Promise<Storyline>;
  starStoryline(id: string): Promise<Storyline>;

  // chat
  /** The user's chats on a book (or in a storyline), most recently active first. */
  listChats(bookId: string, storylineId: string | null): Promise<ChatSummary[]>;
  history(bookId: string, storylineId: string | null, chatId: string): Promise<Message[]>;
  /** Asks in chat `chatId`, or in a new chat when it is null. With `auto` (accept all) the
   *  assistant settles a whole draft in one turn without asking before each batch. */
  send(
    bookId: string,
    storylineId: string | null,
    chatId: string | null,
    text: string,
    onEvent: (e: ChatEvent) => void,
    opts?: { auto?: boolean },
  ): Promise<ChatTurn>;
}

export const api: Api = import.meta.env.VITE_API_MODE === "http" ? httpApi : mockApi;
export const isMock = import.meta.env.VITE_API_MODE !== "http";
