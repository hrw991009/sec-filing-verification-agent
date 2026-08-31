export interface SecReviewDraft {
  readonly accession: string;
  readonly asOf: string;
  readonly cik: string;
  readonly form: "10-K" | "10-Q";
  readonly knowledgeBaseId: string;
  readonly question: string;
  readonly reportPeriod: string;
  readonly scale: number;
  readonly unit: string;
}
