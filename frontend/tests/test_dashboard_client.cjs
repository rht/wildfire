const assert = require('node:assert/strict');
const test = require('node:test');
const { DashboardClient } = require('../legacy/dashboard-client.js');
function state(revision=1) {
  return {schema_version:'coordination-state-1',scenario_id:'s', snapshot_id:'snap',revision,
    as_of:'2026-09-20T10:00:00Z',input_mode:'live',assets:[],contacts:{ranked:[],review:[]},
    calls:[],plan:{locations:[],remaining_capacity:{},response:null},teams:[],tasks:[],events:[],errors:[]};
}
function harness() {
  let latest=state(), now=Date.parse(latest.as_of);
  const sockets=[], timers=[], states=[], statuses=[];
  class Socket {
    constructor(url) {this.url=url; sockets.push(this);}
    close() {this.closed=true;}
    message(data) {this.onmessage({data:JSON.stringify(data)});}
  }
  const client=new DashboardClient({fetchState:async()=>latest, WebSocket:Socket,
    socketUrl:'ws://localhost/api/updates',onState:s=>states.push(s),onStatus:s=>statuses.push(s),
    now:()=>now,setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout:()=>{}});
  return {client,sockets,timers,states,statuses,setLatest:s=>latest=s,setNow:v=>now=v};
}
test('REST cursor catches race, ordered stream ignores duplicate and older states',async()=>{
  const h=harness(); await h.client.start();
  assert.match(h.sockets[0].url,/after_revision=1/);
  h.sockets[0].message(state(2)); h.sockets[0].message(state(2));h.sockets[0].message(state(1));
  h.sockets[0].message(state(5)); // full state safely catches a history gap
  assert.deepEqual(h.states.map(s=>s.revision),[1,2,5]); h.client.stop();
});
test('disconnect shows stale and reconnect fetch catches up including server restart',async()=>{
  const h=harness();await h.client.start();h.sockets[0].message(state(8));
  h.sockets[0].onclose();assert.equal(h.statuses.at(-1).connection,'reconnecting');
  h.setLatest(state(1));await h.timers.at(-1)();
  assert.deepEqual(h.states.map(s=>s.revision),[1,8,1]);
  assert.match(h.sockets.at(-1).url,/after_revision=1/);h.client.stop();
});
test('unknown facts survive; heartbeat cannot conceal old source or system errors',async()=>{
  const h=harness(),s=state();s.calls=[{asset_id:'a',acknowledged:null,wants_human:false,needs_assistance:true}];
  s.errors=[{code:'failed_refresh'}];h.setLatest(s);await h.client.start();
  assert.deepEqual(h.states[0].calls,s.calls);
  h.setNow(Date.parse(s.as_of)+180000);h.sockets[0].message({type:'heartbeat',revision:1});
  assert.equal(h.statuses.at(-1).stale,true);assert.equal(h.statuses.at(-1).errors,1);
  h.sockets[0].message({type:'error',code:'state_unavailable'});
  assert.equal(h.statuses.at(-1).connection,'unavailable');h.client.stop();
});
test('bad JSON retains last good state and schedules recovery',async()=>{
  const h=harness();await h.client.start();h.sockets[0].onmessage({data:'bad'});
  assert.equal(h.states.length,1);assert.equal(h.statuses.at(-1).connection,'unavailable');
  assert.equal(h.sockets[0].closed,true);h.client.stop();
});
module.exports={state};
test('fresh refresh does not hide old or unavailable snapshot evidence',async()=>{
  const h=harness(),s=state();s.snapshot_as_of='2026-09-20T08:00:00Z';h.setLatest(s);
  await h.client.start();assert.equal(h.statuses.at(-1).stale,true);
  h.client.stop();s.snapshot_as_of=s.as_of;s.data_status='unavailable';h.setLatest(s);
  await h.client.start();assert.equal(h.statuses.at(-1).stale,true);h.client.stop();
});
