import {render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {beforeEach,describe,expect,it,vi} from 'vitest';
import NetworkPage from './NetworkPage';

const api=vi.hoisted(()=>({request:vi.fn()}));
vi.mock('../../lib/api/client',()=>({request:api.request}));

const status={initialized:true,telemetry:{scenario:'disconnected',state:'disconnected',timestamp:'2026-10-26T10:00:00Z',latency_ms:5000,packet_loss_pct:100,bandwidth_mbps:0,connected:false,jitter_ms:null,delivery_failure_rate_pct:100,health_score:0,reason:'test outage',simulated:true},queue:{total:0,by_state:{PENDING:0,IN_FLIGHT:0,RETRY_WAIT:0,DELIVERED:0,FAILED:0,EXPIRED:0},active_by_priority:{P0:0,P1:0,P2:0,P3:0}}};
const queue={items:[],offset:0,limit:10,total:0};
const metrics={delivery_success_rate_pct:null,success_rate_denominator:'delivered / terminal',delivery_attempts:0,attempt_outcomes:{},average_simulated_delivery_latency_ms:null,total_original_bytes:0,total_stored_bytes:0,compression_ratio:null,size_reduction_percent:null};

beforeEach(()=>{api.request.mockReset();api.request.mockImplementation(async(path:string,init?:RequestInit)=>{
 if(path==='/network/status')return status;
 if(path.startsWith('/network/messages?'))return queue;
 if(path==='/network/metrics')return metrics;
 if(path==='/network/simulator/scenario'&&init?.method==='POST')return {telemetry:status.telemetry,delivery:{delivered:0}};
 return {};
})});

describe('Network resilience page',()=>{
 it('shows simulated status and role-authorized controls',async()=>{
  render(<NetworkPage user={{role:'NETWORK_OPERATOR'}}/>);
  expect(await screen.findByText('disconnected')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'degraded'})).toBeEnabled();
  expect(screen.getByText(/does not change the host machine's network/i)).toBeInTheDocument();
 });
 it('sends the selected simulator scenario to the API',async()=>{
  const user=userEvent.setup();render(<NetworkPage user={{role:'NETWORK_OPERATOR'}}/>);
  await user.click(await screen.findByRole('button',{name:'recovery'}));
  await waitFor(()=>expect(api.request).toHaveBeenCalledWith('/network/simulator/scenario',expect.objectContaining({method:'POST',body:JSON.stringify({scenario:'recovery'})})));
 });
 it('disables simulator and queue actions for viewers',async()=>{
  render(<NetworkPage user={{role:'VIEWER'}}/>);
  expect(await screen.findByRole('button',{name:'healthy'})).toBeDisabled();
  expect(screen.getByRole('button',{name:/enqueue message/i})).toBeDisabled();
 });
});
