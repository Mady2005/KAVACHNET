export type Role = 'ADMIN' | 'SECURITY_ANALYST' | 'EDGE_OPERATOR' | 'NETWORK_OPERATOR' | 'VIEWER';

export interface User {
  id: string;
  username: string;
  email: string;
  role: Role;
  is_active: boolean;
}

export type SecuritySeverity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'INFO';
export type SecurityPriority = 'P0' | 'P1' | 'P2' | 'P3';
export type SecurityAction = 'ALLOW' | 'WARN' | 'ALERT';
export type SecurityInspectionKind = 'TEXT' | 'DOCUMENT_TEXT';
export type SecurityFindingCategory =
  | 'PROMPT_INJECTION'
  | 'JAILBREAK'
  | 'MALICIOUS_CONTENT'
  | 'CREDENTIAL'
  | 'SENSITIVE_PERSONAL_DATA';
export type SecurityFindingContext = 'DIRECT' | 'EDUCATIONAL';

export interface SecurityFinding {
  rule_id: string;
  category: SecurityFindingCategory;
  occurrences: number;
  score_weight: number;
  context: SecurityFindingContext;
}

export interface SecurityEvidence {
  rules_version: string;
  inspection_kind: SecurityInspectionKind;
  finding_count: number;
  findings: SecurityFinding[];
  redaction_counts: Record<string, number>;
}

export interface Event {
  event_id: string;
  timestamp: string;
  source_module: string;
  event_type: string;
  severity: string;
  security_risk: number;
  situation_risk: number | null;
  network_health: number | null;
  priority: string;
  recommended_action: string;
  status: string;
}

export interface SecurityEvent extends Event {
  source_module: 'SECURITY';
  event_type: 'security.inspection';
  severity: SecuritySeverity;
  priority: SecurityPriority;
  recommended_action: SecurityAction;
  evidence: SecurityEvidence;
  confidence: number | null;
  correlation_id: string;
  simulation: boolean;
}

export interface SecurityInspectionRequest {
  content: string;
  kind?: SecurityInspectionKind;
}

export interface SecurityInspectionResponse {
  event: SecurityEvent;
  findings: SecurityFinding[];
}

export interface SecurityHistoryResponse {
  items: SecurityEvent[];
  offset: number;
  limit: number;
  total: number;
}

export interface Summary {
  total_events: number;
  high_critical_events: number;
  awaiting_action: number;
  by_module: Record<string, number>;
  recent_events: Event[];
  system_status: string;
}
