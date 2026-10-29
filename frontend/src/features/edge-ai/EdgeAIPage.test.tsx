import {render,screen} from '@testing-library/react';
import {beforeEach,describe,it,expect,vi} from 'vitest';
import userEvent from '@testing-library/user-event';
import {request} from '../../lib/api/client';
import EdgeAIPage from './EdgeAIPage';

vi.mock('../../lib/api/client',()=>({request:vi.fn()}));

describe('Edge AI feature',()=>{
  beforeEach(()=>{vi.mocked(request).mockReset()});
  it('shows service status and an honest empty state',async()=>{
    vi.mocked(request).mockResolvedValueOnce({name:'isolation_forest',version:'sklearn-local-v1',available:false,reason:'model not fitted',labeled_accuracy_established:false,ai4i_predictive_maintenance:{name:'AI4I RandomForestClassifier',version:'v1',available:false,reason:'artifact missing',metrics:null}}).mockResolvedValueOnce({items:[],total:0,limit:20,offset:0}).mockResolvedValueOnce({items:[],total:0,limit:10,offset:0});
    render(<EdgeAIPage/>);
    expect(await screen.findByText('isolation_forest · sklearn-local-v1')).toBeInTheDocument();
    expect(screen.getByText(/No readings in stored history yet/)).toBeInTheDocument();
  });
  it('shows a simulated result and evidence after the API responds',async()=>{
    const item={event:{event_id:'event-1',timestamp:'2026-01-01T00:00:00Z',severity:'HIGH',priority:'P1',simulation:true,situation_risk:0.85,evidence:{sensor_id:'pump-7'}},analysis:{sensor_id:'pump-7',timestamp:'2026-01-01T00:00:00Z',anomaly:true,anomaly_score:0.85,score_semantics:'not a probability',explanation:'Temperature exceeded threshold.',detection_methods:['rules'],triggered_rules:['TEMP_HIGH'],features:{temperature_c:96},data_quality_warnings:[]}};
    vi.mocked(request).mockResolvedValueOnce({name:'isolation_forest',version:'sklearn-local-v1',available:false,reason:'model not fitted',labeled_accuracy_established:false,ai4i_predictive_maintenance:{name:'AI4I RandomForestClassifier',version:'v1',available:false,reason:'artifact missing',metrics:null}}).mockResolvedValueOnce({items:[],total:0,limit:20,offset:0}).mockResolvedValueOnce({items:[],total:0,limit:10,offset:0}).mockResolvedValueOnce({items:[]}).mockResolvedValueOnce({items:[item],total:1,limit:20,offset:0}).mockResolvedValueOnce({items:[],total:0,limit:10,offset:0});
    render(<EdgeAIPage/>);
    await userEvent.click(screen.getByRole('button',{name:'Run labeled simulation'}));
    expect(await screen.findByText('pump-7')).toBeInTheDocument();
    expect(screen.getByText('SIMULATED')).toBeInTheDocument();
    expect(screen.getByText('Temperature exceeded threshold.')).toBeInTheDocument();
  });
  it('submits the AI4I feature contract and shows the backend prediction',async()=>{
    vi.mocked(request).mockResolvedValueOnce({name:'isolation_forest',version:'local',available:true,reason:null,labeled_accuracy_established:false,ai4i_predictive_maintenance:{name:'AI4I RandomForestClassifier',version:'trained-v1',available:true,reason:null,metrics:{recall_positive:0.41}}}).mockResolvedValueOnce({items:[],total:0,limit:20,offset:0}).mockResolvedValueOnce({items:[],total:0,limit:10,offset:0}).mockResolvedValueOnce({prediction:1,class_probabilities:{'0':0.2,'1':0.8},probabilities_calibrated:false});
    render(<EdgeAIPage/>);
    await userEvent.click(await screen.findByRole('button',{name:'Predict machine failure'}));
    expect(await screen.findByText('Predicted machine failure: 1')).toBeInTheDocument();
    expect(vi.mocked(request).mock.calls[3][0]).toBe('/edge-ai/predict/maintenance');
  });
  it('shows a service error when live API reads fail',async()=>{
    vi.mocked(request).mockRejectedValueOnce(new Error('Backend unavailable')).mockRejectedValueOnce(new Error('Backend unavailable'));
    render(<EdgeAIPage/>);
    expect(await screen.findByRole('alert')).toHaveTextContent('Backend unavailable');
  });
  it('renders persisted incidents, opens linked evidence and acknowledges them',async()=>{
    const incident={incident_id:'inc-1',title:'pump-7: temperature_c',category:'sensor_anomaly:temperature_c',severity:'HIGH',status:'OPEN',source_id:'pump-7',first_observed:'2026-01-01T00:00:00Z',last_observed:'2026-01-01T00:00:00Z',event_count:1};
    vi.mocked(request).mockResolvedValueOnce({name:'isolation_forest',version:'local',available:false,reason:null,labeled_accuracy_established:false,ai4i_predictive_maintenance:{name:'model',version:'v1',available:false,reason:'missing',metrics:null}}).mockResolvedValueOnce({items:[],total:0,limit:20,offset:0}).mockResolvedValueOnce({items:[incident],total:1,limit:10,offset:0}).mockResolvedValueOnce({incident,events:[{event:{event_id:'evt-1',event_type:'sensor_anomaly',severity:'HIGH',timestamp:'2026-01-01T00:00:00Z',recommended_action:'ALERT',evidence:{temperature_c:96}},observation:{observation_id:'obs-1',measurements:{temperature_c:96}}}],status_history:[]}).mockResolvedValueOnce({...incident,status:'ACKNOWLEDGED'}).mockResolvedValueOnce({items:[{...incident,status:'ACKNOWLEDGED'}],total:1,limit:10,offset:0}).mockResolvedValueOnce({incident:{...incident,status:'ACKNOWLEDGED'},events:[],status_history:[]});
    render(<EdgeAIPage/>);
    expect(await screen.findByText('pump-7: temperature_c')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button',{name:'View incident details'}));
    expect(await screen.findByText(/"observation_id"/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button',{name:'Acknowledge'}));
    expect(vi.mocked(request).mock.calls.some(([path,init])=>path==='/incidents/inc-1/status'&&init?.method==='PATCH')).toBe(true);
  });
});
