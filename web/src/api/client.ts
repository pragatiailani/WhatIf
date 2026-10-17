import type {
  Book,
  ChatTurn,
  Checkpoint,
  IngestionProgress,
  Message,
  Session,
  StoryGraph,
  Storyline,
  User,
  Visibility,
} from "./types";
import { mockApi } from "./mock/server";
import { httpApi } from "./http";

/** Streamed while a chat turn is being produced. */
export type ChatEvent =
  | { type: "phase"; label: string }
  | { type: "token"; text: string };

export interface Api {
  // auth
  signUp(name: string, email: string, password: string): Promise<Session>;
  signIn(email: string, password: string): Promise<Session>;
  me(token: string): Promise<User>;

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
  forkStoryline(id: string): Promise<Storyline>;
  updateStoryline(id: string, patch: { title?: string; visibility?: Visibility }): Promise<Storyline>;
  starStoryline(id: string): Promise<Storyline>;

  // checkpoints
  listCheckpoints(storylineId: string): Promise<Checkpoint[]>;
  saveCheckpoint(storylineId: string, name: string): Promise<Checkpoint>;
  restoreCheckpoint(storylineId: string, checkpointId: string): Promise<{ storyline: Storyline; graph: StoryGraph }>;

  // chat
  history(bookId: string, storylineId: string | null): Promise<Message[]>;
  send(
    bookId: string,
    storylineId: string | null,
    text: string,
    onEvent: (e: ChatEvent) => void,
  ): Promise<ChatTurn>;
}

export const api: Api = import.meta.env.VITE_API_MODE === "http" ? httpApi : mockApi;
export const isMock = import.meta.env.VITE_API_MODE !== "http";
