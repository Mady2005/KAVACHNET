export type Role='ADMIN'|'SECURITY_ANALYST'|'EDGE_OPERATOR'|'NETWORK_OPERATOR'|'VIEWER';
export interface User {id:string;username:string;email:string;role:Role;is_active:boolean}
export interface Event {event_id:string;timestamp:string;source_module:string;event_type:string;severity:string;security_risk:number;situation_risk:number;network_health:number;priority:string;recommended_action:string;status:string}
export interface Summary {total_events:number;high_critical_events:number;awaiting_action:number;by_module:Record<string,number>;recent_events:Event[];system_status:string}
