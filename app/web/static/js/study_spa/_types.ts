// Study SPA type definitions. Pure types, no runtime emission.

export type StudyPhase = 'front' | 'back' | 'done';
export type StudyCardType = 'new' | 'learning' | 'review';

export interface StudyCardIntervals {
  again: string;
  hard: string;
  good: string;
  easy: string;
}

export interface StudyCard {
  card_id: number;
  deck_id: number;
  question_html: string;
  answer_html: string;
  is_cloze: boolean;
  note_type_name: string;
  flag: number;
  is_marked: boolean;
  intervals: StudyCardIntervals | null;
}

export interface StudyUndo {
  label: string | null;
  can_undo: boolean;
  can_redo: boolean;
}

export interface StudyState {
  phase: StudyPhase;
  deck_id: number;
  is_filtered: boolean;
  has_cards: boolean;
  undo: StudyUndo;
  error_msg: string;
  card: StudyCard | null;
  remaining_total: number;
  remaining_new: number;
  remaining_learn: number;
  remaining_review: number;
  card_type: StudyCardType;
  sync_required: boolean;
}

export interface StudySpaConfig {
  deckId: number;
  isFiltered: boolean;
  csrfToken: string;
}

export interface StudyOkResponse {
  ok: true;
  count: number;
}

export interface StudyMarkResponse {
  is_marked: boolean;
}

export interface StudyFlagResponse {
  flag: number;
}

export interface StudyApiAuthError {
  message: 'unauth' | 'csrf' | 'spa-disabled';
}

export interface StudyApiBadError {
  message: string;
  code: number;
  body: { error_msg?: string };
}

export type StudyApiError = StudyApiAuthError | StudyApiBadError;

export type ElChild = Node | string | null | false | undefined;
