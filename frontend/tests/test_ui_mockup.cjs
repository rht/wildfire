const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { JSDOM } = require('jsdom');
const html=fs.readFileSync(path.join(__dirname,'../legacy/ui-mockup.html'),'utf8');
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
 const markers=[],lines=[],geometryLayers=[],fitted=[];
 const layer=()=>({addTo(){return this;},clearLayers(){}});
 window.L={map:()=>({setView(){return this;},invalidateSize(){},fitBounds(points){fitted.push(points);}}),tileLayer:layer,layerGroup:layer,
 circleMarker:()=>{const m={...layer(),bindTooltip(t){this.tooltip=t;return this;},on(k,f){this.click=f;return this;}};markers.push(m);return m;},
 geoJSON:(g,options)=>{lines.push(g);geometryLayers.push({geometry:g,options});return layer();},polyline:(g)=>{lines.push(g);return layer();}};
 window.eval(fs.readFileSync(path.join(__dirname,'../legacy/dashboard-view.js'),'utf8'));
 return {dom,window,document:window.document,markers,lines,geometryLayers,fitted,view:window.DashboardView};
}
test('supplied fire perimeter retains a white halo without introducing fixture geometry',()=>{
 const h=load(),s=sample();h.view.render(s);assert.equal(h.geometryLayers.length,0);
 s.fire_geometry={type:'Polygon',coordinates:[[[3.1,41.9],[3.2,41.9],[3.2,42],[3.1,41.9]]]};
 h.view.render(s);assert.equal(h.geometryLayers.length,2);
 for(const layer of h.geometryLayers) assert.equal(layer.geometry,s.fire_geometry);
 assert.equal(h.geometryLayers[0].options.color,'#fff');
 assert.equal(h.geometryLayers[0].options.fill,false);
 assert.ok(h.geometryLayers[0].options.weight>h.geometryLayers[1].options.weight);
 assert.equal(h.geometryLayers[1].options.color,'#ff5a3c');h.dom.window.close();
});
test('hostile IDs, review reasons and event notes stay literal through review selection',()=>{
 const h=load(),s=sample(),hostile='a"><img data-injected src=x>';
 s.assets[0].asset_id=hostile;s.assets[0].asset_type='<svg data-injected>';
 s.contacts.ranked=s.contacts.ranked.filter(c=>c.asset_id!=='a');
 s.contacts.review=[{asset_id:hostile,review_reasons:['<script data-injected>bad</script>']}];
 s.calls[0].asset_id=hostile;s.calls[0].wants_human=true;
 s.events=[{kind:'refresh',as_of:s.as_of,notes:'<img data-injected src=x>'}];
 h.view.render(s);h.document.querySelector('.qrow').click();
 assert.ok(h.document.getElementById('detailPanel').textContent.includes(hostile));
 assert.match(h.document.querySelector('.qrow').textContent,/<script data-injected>/);
 h.document.getElementById('changeLogBtn').click();
 assert.match(h.document.getElementById('changeLog').textContent,/<img data-injected/);
 assert.equal(h.document.querySelectorAll('[data-injected]').length,0);
 assert.equal(h.document.querySelector('#escOpenList .escRow').dataset.assetId,hostile);
 h.dom.window.close();
});
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
 const h=load(),s=JSON.parse(fs.readFileSync(path.join(__dirname,'../../fixtures/dashboard/coordination-export.json'),'utf8'));
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
test('no answer shows a pending callback without inventing a human or assistance request',()=>{
 const h=load(),s=sample();s.calls=[{asset_id:'a',status:'no_answer',wants_human:null,
 reported_needs_assistance:null,can_self_evacuate:null,acknowledged:null}];
 s.tasks=[{task_id:'callback-a',asset_id:'a',kind:'human_callback',status:'open'}];
 h.view.render(s);
 assert.equal(h.document.getElementById('escOpen').textContent,'0');
 assert.equal(h.document.getElementById('escMobility').textContent,'0');
 const row=h.document.querySelector('#escCallbackList .escRow');
 assert.match(row.textContent,/First.*no_answer.*open/);row.click();
 assert.match(h.document.getElementById('detailPanel').textContent,/Can self evacuateunknown/);
 s.tasks[0].status='done';h.view.render(s);
 assert.equal(h.document.querySelectorAll('#escCallbackList .escRow').length,0);
 h.dom.window.close();
});
test('nested ledger allocation renders reservation and permission separately from evacuation status',()=>{
 const h=load(),s=sample();s.plan.locations=[{asset_id:'a',mode:'self_evacuate',evacuation_status:'not_confirmed',
 allocations:[{allocation_id:'alloc-a',state:'reserved',destination_id:'hall',instruction_allowed:true,
 safety:'valid',assistance:{transport_confirmed:null,reception_confirmed:null}}]}];
 h.view.render(s);h.document.querySelector('[data-asset-id="a"]').click();
 const text=h.document.getElementById('detailPanel').textContent;
 for(const expected of ['Allocationalloc-a','Allocation statereserved','Destinationhall','Instruction allowedyes','Evacuation statusnot_confirmed']) assert.ok(text.includes(expected),expected);
 h.dom.window.close();
});

test('initial map fits supplied locations without resetting analyst view on each revision',()=>{
 const h=load(),s=sample();h.view.render(s);
 assert.equal(h.fitted.length,1);
 assert.deepEqual(Array.from(h.fitted[0],p=>Array.from(p)),[[41.9,3.1],[41.9,3.1]]);
 s.revision=2;h.view.render(s);assert.equal(h.fitted.length,1);
 s.scenario_id='new-scenario';h.view.render(s);assert.equal(h.fitted.length,2);
 h.dom.window.close();
});
