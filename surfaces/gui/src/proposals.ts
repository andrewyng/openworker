export interface ProposalGroup {
  id: string;
  title: string;
  summary: string;
}
export interface ExternalActions {
  status: "none" | "planned" | "undetermined";
  actions: string[];
  exclusions: string[];
  explanation: string;
}
export interface ProposalTask {
  key?: string;
  title: string;
  criteria: string;
  description?: string;
  activity?: string;
  workstream?: string;
  depends_on?: string[];
  verifies?: string[];
}
