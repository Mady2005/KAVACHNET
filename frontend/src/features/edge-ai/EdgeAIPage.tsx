import {useEffect, useState, type FormEvent} from 'react';
import {request} from '../../lib/api/client';
import './edge-ai.css';

type KavachEvent={event_id:string;timestamp:string;severity:string;priority:string;simulation:boolean;situation_risk:number;evidence:Record<string,unknown>};
type Analysis={sensor_id:string;timestamp:string;anomaly:boolean;anomaly_score:number;score_semantics:string;explanation:string;detection_methods:string[];triggered_rules:string[];features:Record<string,number>;data_quality_warnings:string[]};
type Result={event:KavachEvent;analysis:Analysis};
type PredictiveModel={name:string;version:string;available:boolean;reason:string|null;target:string;dataset_sha256:string|null;trained_at_utc:string|null;probabilities_calibrated:boolean;decision_threshold:number|null;metrics:Record<string,unknown>|null};
type ModelInfo={name:string;version:string;available:boolean;reason:string|null;labeled_accuracy_established:boolean;ai4i_predictive_maintenance:PredictiveModel};
type HistoryPage={items:Result[];total:number;limit:number;offset:number};
type Incident={incident_id:string;title:string;category:string;severity:string;status:string;source_id:string;first_observed:string;last_observed:string;event_count:number};
type IncidentPage={items:Incident[];total:number;limit:number;offset:number};
const emptyPrediction={product_type:'M',air_temperature_k:'300',process_temperature_k:'310',rotational_speed_rpm:'1500',torque_nm:'40',tool_wear_min:'100'};

export default function EdgeAIPage(){
  const [model,setModel]=useState<ModelInfo|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[results,setResults]=useState<Result[]>([]),[historyTotal,setHistoryTotal]=useState(0);
  const [sensorId,setSensorId]=useState(''),[temperature,setTemperature]=useState(''),[humidity,setHumidity]=useState(''),[vibration,setVibration]=useState('');
  const [prediction,setPrediction]=useState(emptyPrediction),[predictionResult,setPredictionResult]=useState<Record<string,unknown>|null>(null),[predictBusy,setPredictBusy]=useState(false);
  const [incidents,setIncidents]=useState<Incident[]>([]),[incidentTotal,setIncidentTotal]=useState(0),[incidentOffset,setIncidentOffset]=useState(0),[incidentStatus,setIncidentStatus]=useState(''),[incidentLoading,setIncidentLoading]=useState(true),[incidentError,setIncidentError]=useState(''),[selectedIncident,setSelectedIncident]=useState<Record<string,any>|null>(null);
  async function refreshHistory(){const page=await request<HistoryPage>('/edge-ai/history?limit=20');setResults(page.items);setHistoryTotal(page.total)}
  async function refreshIncidents(status=incidentStatus,offset=incidentOffset){setIncidentLoading(true);setIncidentError('');try{const query=new URLSearchParams({limit:'10',offset:String(offset)});if(status)query.set('status',status);const page=await request<IncidentPage>(`/incidents?${query}`);setIncidents(Array.isArray(page?.items)?page.items:[]);setIncidentTotal(page?.total??0);setIncidentOffset(offset)}catch(e){setIncidentError(e instanceof Error?e.message:'Unable to load incidents.')}finally{setIncidentLoading(false)}}
  useEffect(()=>{request<ModelInfo>('/edge-ai/model-info').then(setModel).catch(e=>setError(e.message));refreshHistory().catch(e=>setError(e.message));refreshIncidents('',0)},[]);
  async function submit(e:FormEvent){e.preventDefault();setError('');setBusy(true);try{
    const reading:Record<string,unknown>={sensor_id:sensorId.trim(),timestamp:new Date().toISOString(),simulation:false};
    if(temperature!=='')reading.temperature_c=Number(temperature);if(humidity!=='')reading.humidity_pct=Number(humidity);if(vibration!=='')reading.vibration_mm_s=Number(vibration);
    await request('/edge-ai/analyze',{method:'POST',body:JSON.stringify(reading)});await refreshHistory();await refreshIncidents();
  }catch(x){setError(x instanceof Error?x.message:'Unable to analyze the reading.')}finally{setBusy(false)}}
  async function simulate(){setError('');setBusy(true);try{await request('/edge-ai/simulate',{method:'POST',body:JSON.stringify({count:6,seed:42})});await refreshHistory();await refreshIncidents()}catch(x){setError(x instanceof Error?x.message:'Unable to run the simulation.')}finally{setBusy(false)}}
  async function showIncident(id:string){try{setSelectedIncident(await request(`/incidents/${id}`))}catch(e){setIncidentError(e instanceof Error?e.message:'Unable to load incident details.')}}
  async function updateIncident(id:string,status:'ACKNOWLEDGED'|'RESOLVED'){try{await request(`/incidents/${id}/status`,{method:'PATCH',body:JSON.stringify({status})});await refreshIncidents();if(selectedIncident?.incident?.incident_id===id)await showIncident(id)}catch(e){setIncidentError(e instanceof Error?e.message:'Unable to update incident status.')}}
  async function predict(e:FormEvent){e.preventDefault();setError('');setPredictBusy(true);setPredictionResult(null);try{const body={product_type:prediction.product_type,air_temperature_k:Number(prediction.air_temperature_k),process_temperature_k:Number(prediction.process_temperature_k),rotational_speed_rpm:Number(prediction.rotational_speed_rpm),torque_nm:Number(prediction.torque_nm),tool_wear_min:Number(prediction.tool_wear_min)};setPredictionResult(await request('/edge-ai/predict/maintenance',{method:'POST',body:JSON.stringify(body)}))}catch(x){setError(x instanceof Error?x.message:'Unable to run predictive maintenance inference.')}finally{setPredictBusy(false)}}
  return <div className="content">
    <div className="page-head"><div><p className="eyebrow">SITUATIONAL INTELLIGENCE</p><h1>Edge Intelligence</h1><p className="muted">Local sensor analysis with traceable evidence. Findings are decision support.</p></div><span className="status">{model?.available?'ML model active':'Rules available'}</span></div>
    {error&&<div className="error" role="alert">{error}</div>}
    <div className="grid-two edge-panels">
      <section className="panel"><h2>Analyze a sensor reading</h2><p className="muted">Timestamp is recorded at submission. Enter at least one measurement.</p>
        <form className="edge-form" onSubmit={submit}>
          <label>Sensor ID<input required maxLength={128} value={sensorId} onChange={e=>setSensorId(e.target.value)} placeholder="pump-7"/></label>
          <label>Temperature (°C)<input type="number" step="any" value={temperature} onChange={e=>setTemperature(e.target.value)}/></label>
          <label>Humidity (%)<input type="number" min="0" max="100" step="any" value={humidity} onChange={e=>setHumidity(e.target.value)}/></label>
          <label>Vibration (mm/s)<input type="number" min="0" step="any" value={vibration} onChange={e=>setVibration(e.target.value)}/></label>
          <button className="primary" disabled={busy}>{busy?'Analyzing…':'Analyze reading'}</button>
        </form>
      </section>
      <section className="panel"><h2>Service and model status</h2>{model?<><div className="edge-status-row"><span>Rule engine</span><b>Available</b></div><div className="edge-status-row"><span>Detector</span><b>{model.name} · {model.version}</b></div><div className="edge-status-row"><span>AI4I failure model</span><b>{model.ai4i_predictive_maintenance.available?'Available':'Unavailable'}</b></div><p className="muted">{model.ai4i_predictive_maintenance.reason??'Trained locally from the UCI AI4I dataset.'}</p><p className="muted">Failure recall: {String((model.ai4i_predictive_maintenance.metrics as Record<string,unknown>|null)?.recall_positive??'not reported')} · probabilities calibrated: no</p></>:<p className="muted" role="status">Loading model status…</p>}<button className="secondary" disabled={busy} onClick={simulate}>Run labeled simulation</button></section>
    </div>
    <section className="panel"><h2>Predictive maintenance</h2><p className="muted">AI4I 2020 model predicts the Machine failure label. Probabilities are uncalibrated decision support, not a safety control.</p>
      <form className="edge-form" onSubmit={predict}><label>Product type<select value={prediction.product_type} onChange={e=>setPrediction({...prediction,product_type:e.target.value})}><option>L</option><option>M</option><option>H</option></select></label>
        {([['air_temperature_k','Air temperature (K)'],['process_temperature_k','Process temperature (K)'],['rotational_speed_rpm','Rotational speed (rpm)'],['torque_nm','Torque (Nm)'],['tool_wear_min','Tool wear (min)']] as const).map(([key,label])=><label key={key}>{label}<input required type="number" step="any" value={prediction[key]} onChange={e=>setPrediction({...prediction,[key]:e.target.value})}/></label>)}
        <button className="primary" disabled={predictBusy||!model?.ai4i_predictive_maintenance.available}>{predictBusy?'Predicting…':'Predict machine failure'}</button></form>
      {predictionResult&&<div role="status" className="edge-result"><strong>Predicted machine failure: {String(predictionResult.prediction)}</strong><p>Class probabilities: {JSON.stringify(predictionResult.class_probabilities)} · not calibrated</p><p>Decision threshold: {String(predictionResult.decision_threshold)}</p></div>}
    </section>
    <section className="panel events edge-results"><div className="page-head"><div><h2>Recent analysis results</h2><p className="muted">Persisted sensor observations · {historyTotal} total. Sensor history and event persistence are separate.</p></div></div>
      {!results.length&&!busy&&<div className="empty">No readings in stored history yet. Submit a reading or run the simulation.</div>}
      {busy&&!results.length&&<p className="muted" role="status">Waiting for the Edge AI service…</p>}
      {results.map(({event,analysis})=><article className="edge-result" key={event.event_id}>
        <div className="edge-result-head"><div><strong>{analysis.sensor_id}</strong><span className={`badge ${event.severity.toLowerCase()}`}>{analysis.anomaly?'ANOMALY':'NORMAL'}</span>{event.simulation&&<span className="badge">SIMULATED</span>}</div><time>{new Date(analysis.timestamp).toLocaleString()}</time></div>
        <p>{analysis.explanation}</p><div className="edge-status-row"><span>Severity / priority</span><b>{event.severity} / {event.priority}</b></div><div className="edge-status-row"><span>Situation risk</span><b>{event.situation_risk} · {analysis.score_semantics}</b></div>
        <div className="edge-status-row"><span>Observed measurements</span><b>{Object.entries(analysis.features).map(([key,value])=>`${key.replace(/_/g,' ')}: ${value}`).join(' · ')||'None'}</b></div>
        <div className="edge-status-row"><span>Detection methods</span><b>{analysis.detection_methods.join(', ')||'None'}</b></div>
        {!!analysis.triggered_rules.length&&<div className="edge-status-row"><span>Triggered rules</span><b>{analysis.triggered_rules.join(', ')}</b></div>}
        {!!analysis.data_quality_warnings.length&&<div className="edge-quality">Data quality: {analysis.data_quality_warnings.join(', ')}</div>}
        <details><summary>Event evidence</summary><pre>{JSON.stringify(event.evidence,null,2)}</pre></details>
      </article>)}
    </section>
    <section className="panel events" aria-labelledby="incidents-title"><div className="page-head"><div><h2 id="incidents-title">Persisted incidents</h2><p className="muted">Correlated canonical events · {incidentTotal} total.</p></div><label>Filter status<select aria-label="Filter incidents by status" value={incidentStatus} onChange={e=>{setIncidentStatus(e.target.value);refreshIncidents(e.target.value,0)}}><option value="">All statuses</option><option value="OPEN">Open</option><option value="ACKNOWLEDGED">Acknowledged</option><option value="RESOLVED">Resolved</option></select></label></div>
      {incidentError&&<div role="alert" className="error">{incidentError}</div>}{incidentLoading&&<p role="status" className="muted">Loading incidents…</p>}
      {!incidentLoading&&!incidentError&&!incidents.length&&<div className="empty">No persisted incidents match this filter.</div>}
      {incidents.map(i=><article className="edge-result" key={i.incident_id}><div className="edge-result-head"><div><strong>{i.title}</strong><span className={`badge ${i.severity.toLowerCase()}`}>{i.severity}</span><span className="badge">{i.status}</span></div><time>{new Date(i.last_observed).toLocaleString()}</time></div><div className="edge-status-row"><span>Device / category</span><b>{i.source_id} · {i.category}</b></div><div className="edge-status-row"><span>First observed · linked events</span><b>{new Date(i.first_observed).toLocaleString()} · {i.event_count}</b></div><button className="secondary" onClick={()=>showIncident(i.incident_id)}>View incident details</button>{i.status==='OPEN'&&<button className="secondary" onClick={()=>updateIncident(i.incident_id,'ACKNOWLEDGED')}>Acknowledge</button>}{i.status!=='RESOLVED'&&<button className="secondary" onClick={()=>updateIncident(i.incident_id,'RESOLVED')}>Resolve</button>}</article>)}
      {incidentTotal>10&&<div className="edge-status-row"><button className="secondary" disabled={incidentOffset===0||incidentLoading} onClick={()=>refreshIncidents(incidentStatus,Math.max(0,incidentOffset-10))}>Previous</button><span>{incidentOffset+1}–{Math.min(incidentOffset+10,incidentTotal)} of {incidentTotal}</span><button className="secondary" disabled={incidentOffset+10>=incidentTotal||incidentLoading} onClick={()=>refreshIncidents(incidentStatus,incidentOffset+10)}>Next</button></div>}
      {selectedIncident&&<div className="edge-result" aria-label="Incident detail"><button className="secondary" onClick={()=>setSelectedIncident(null)}>Close details</button><h3>{selectedIncident.incident?.title}</h3><p>Status history: {selectedIncident.status_history?.map((h:any)=>`${h.from_status??'Created'} → ${h.to_status}`).join(' · ')}</p>{selectedIncident.events?.map((entry:any)=><details key={entry.event.event_id} open><summary>{entry.event.event_type} · {entry.event.severity} · {entry.event.timestamp}</summary><p>{entry.event.recommended_action}</p><pre>{JSON.stringify({evidence:entry.event.evidence,observation:entry.observation},null,2)}</pre></details>)}</div>}
    </section>
  </div>
}
