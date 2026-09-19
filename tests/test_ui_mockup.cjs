const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { JSDOM } = require('jsdom');
const html=fs.readFileSync(path.join(__dirname,'../design/ui-mockup.html'),'utf8');
function sample() {
 return {schema_version:'coordination-state-1',scenario_id:'s',snapshot_id:'snap',revision:1,
 as_of:'2026-09-20T10:00:00Z',input_mode:'offline_demo',
 assets:[{asset_id:'a',name:'First',latitude:41.9,longitude:3.1,estimated_occupancy:null,sources:[]},
 {asset_id:'b',name:'Second',latitude:41.9,longitude:3.1,sources:[]}],
 contacts:{ranked:[{asset_id:'b',rank:1,slack_min:8,status:'window_open'},
 {asset_id:'a',rank:2,slack_min:-9,status:'window_exhausted'}],review:[]},
 calls:[{request_id:'r',asset_id:'a',status:'completed',needs_assistance:true,wants_human:false,acknowledged:null,
 departure_confirmed:null,arrival_confirmed:false}],
 plan:{locations:[],remaining_capacity:{},response:null},teams:[],tasks:[],events:[],errors:[]};
}
function load() {
 const dom=new JSDOM(html,{runScripts:'outside-only',url:'http://localhost:8521/'}),{window}=dom;
 const markers=[],lines=[];
 const layer=()=>({addTo(){return this;},clearLayers(){}});
 window.L={map:()=>({setView(){return this;},invalidateSize(){}}),tileLayer:layer,layerGroup:layer,
 circleMarker:()=>{const m={...layer(),bindTooltip(t){this.tooltip=t;return this;},on(k,f){this.click=f;return this;}};markers.push(m);return m;},
 geoJSON:(g)=>{lines.push(g);return layer();},polyline:(g)=>{lines.push(g);return layer();}};
 window.eval(fs.readFileSync(path.join(__dirname,'../design/dashboard-view.js'),'utf8'));
 return {dom,window,document:window.document,markers,lines,view:window.DashboardView};
}
test('uses backend contact order and stable asset IDs; preserves unknowns and separate call facts',()=>{
 const h=load();h.view.render(sample());
 assert.deepEqual([...h.document.querySelectorAll('#rankedList .row')].map(x=>x.dataset.assetId),['b','a']);
 h.document.querySelector('[data-asset-id="a"]').click();
 const text=h.document.getElementById('detailPanel').textContent;
 for(const expected of ['estimated occupancyunknown','Call statuscompleted','Assistance requestedyes',
 'Human requestedno','Message acknowledgedunknown','Departure confirmedunknown','Arrival confirmedno']) assert.ok(text.includes(expected),expected);
 assert.match(h.document.getElementById('modeBadge').textContent,/offline/i);
 h.dom.window.close();
});
test('safe text and marker selection survive updates; no UI control asserts dispatch or assignment',()=>{
 const h=load(),s=sample();s.assets[0].name='<img data-injected src=x>';
 s.assets[0].sources=[{fields:['occupancy'],source:'<script data-injected>bad</script>',notes:'provenance'}];
 h.view.render(s);h.markers.find(x=>x.assetId==='a').click({target:{assetId:'a'}});
 assert.match(h.document.getElementById('detailPanel').textContent,/<img data-injected/);
 assert.equal(h.document.querySelectorAll('[data-injected]').length,0);
 assert.ok(h.markers.find(x=>x.assetId==='a').tooltip instanceof h.window.HTMLElement);
 s.revision=2;s.calls[0].wants_human=true;h.view.render(s);
 assert.match(h.document.getElementById('detailPanel').textContent,/Human requestedyes/);
 for(const id of ['notifyPhoneBtn','notifyRadioBtn','notifyFireDeptBtn']) assert.equal(h.document.getElementById(id).disabled,true);
 assert.equal(h.document.querySelectorAll('.taskActions button').length,0);
 h.dom.window.close();
});
test('database, change log, tilt, missing coordinates and supplied paths remain usable',()=>{
 const h=load(),s=sample();s.assets[0].latitude=null;s.assets[0].longitude=null;
 s.events=[{kind:'refresh',as_of:s.as_of,notes:'new snapshot'}];
 s.plan.locations=[{asset_id:'b',routes:[{route_id:'route',status:'proposed',source:'analyst',path:[[41.9,3.1],[41.92,3.12]]}]}];
 h.view.render(s);assert.equal(h.markers.length,1);assert.equal(h.lines.length,1);
 h.document.getElementById('openDbBtn').click();assert.equal(h.document.querySelectorAll('#dbTableBody tr').length,2);
 h.document.getElementById('backToMapBtn').click();h.document.getElementById('changeLogBtn').click();
 assert.equal(h.document.getElementById('changeLog').classList.contains('open'),true);
 assert.match(h.document.getElementById('changeLog').textContent,/new snapshot/);
 h.document.getElementById('toggle3dBtn').click();assert.equal(h.document.getElementById('map').classList.contains('tilt3d'),true);
 h.dom.window.close();
});
test('removed selected asset clears detail and absent routes never get invented',()=>{
 const h=load(),s=sample();h.view.render(s);h.document.querySelector('[data-asset-id="a"]').click();
 assert.equal(h.lines.length,0);s.assets=s.assets.filter(x=>x.asset_id!=='a');s.contacts.ranked=s.contacts.ranked.slice(0,1);
 h.view.render(s);assert.match(h.document.getElementById('detailPanel').textContent,/Select a location/);h.dom.window.close();
});
test('verified coordination export displays actual plan status and proposed response timings',()=>{
 const h=load(),s=JSON.parse(fs.readFileSync(path.join(__dirname,'../fixtures/dashboard/coordination-export.json'),'utf8'));
 h.view.render(s);h.document.querySelector('[data-asset-id="A"]').click();
 const text=h.document.getElementById('detailPanel').textContent;
 assert.match(text,/Evacuation statusnot_confirmed/);assert.match(text,/Plan modeundetermined/);
 assert.match(h.document.getElementById('responsePlan').textContent,/act_C/);
 assert.match(h.document.getElementById('responsePlan').textContent,/Proposed departure \(min\)0/);
 h.dom.window.close();
});
test('multi-crew paths use supplied lonlat geometry and retain proposed status',()=>{
 const h=load(),s=sample();s.plan.response={schema_version:'multi-response-plan-1',teams:[
 {team_id:'truck',tasks:[{action_id:'evac',asset_id:'a',status:'proposed',depart_min:3,finish_min:9,
 route_source:'verified graph fixture',path_lonlat:[[3.1,41.9],[3.2,41.8]]}]}]};
 h.view.render(s);assert.deepEqual(Array.from(h.lines[0],p=>Array.from(p)),[[41.9,3.1],[41.8,3.2]]);
 assert.match(h.document.getElementById('responsePlan').textContent,/proposed/);h.dom.window.close();
});
test('allocation lifecycle and assistance confirmations display supplied backend facts',()=>{
 const h=load(),s=sample();s.plan.locations=[{asset_id:'a',allocation_id:'alloc',state:'departed',
 destination_id:'shelter',instruction_allowed:false,safety:'review',assistance:{transport_confirmed:true,reception_confirmed:null,pickup_min:4}}];
 h.view.render(s);h.document.querySelector('[data-asset-id="a"]').click();
 const text=h.document.getElementById('detailPanel').textContent;
 assert.match(text,/Allocation statedeparted/);assert.match(text,/Transport confirmedyes/);
 assert.match(text,/Reception confirmedunknown/);h.dom.window.close();
});
test('response panel exposes uncovered and unassigned assets with backend reasons',()=>{
 const h=load(),s=sample();s.plan.response={coverage:{a:0},unserved:{a:'needs additional resources'},
 unassigned:[{asset_id:'b',reasons:['no_available_team']}],review:[{asset_id:'a',reason:'unknown_transport'}],
 blocked_actions:{'act-a':['missing_route']}};
 h.view.render(s);const text=h.document.getElementById('responsePlan').textContent;
 for(const reason of ['needs additional resources','no_available_team','unknown_transport','missing_route']) assert.ok(text.includes(reason),reason);
 h.dom.window.close();
});
