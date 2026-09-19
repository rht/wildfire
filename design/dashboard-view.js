/* Michella's dashboard presentation, fed only by public coordination state. */
(function(root) {
  'use strict';
  const $=id=>document.getElementById(id);
  const value=x=>x===null || x===undefined ? 'unknown' : String(x);
  const flag=x=>x===true ? 'yes' : x===false ? 'no' : 'unknown';
  const time=x=>x && Number.isFinite(Date.parse(x)) ? new Date(x).toISOString().replace('.000Z','Z') : 'unknown';
  function el(tag,text,cls='') {
    const node=document.createElement(tag);node.className=cls;
    if(text!==undefined) node.textContent=value(text);
    return node;
  }
  function kv(rows) {
    const box=el('div',undefined,'kv');
    for(const [label,text] of rows) box.append(el('div',label,'k'),el('div',text,'v'));
    return box;
  }
  let current,selectedId=null,dbOpen=false,map,markers,routes,fire;
  function initMap() {
    if(!root.L) {$('map').textContent='Map unavailable. Locations and evidence remain available in the list.';return;}
    map=L.map('map').setView([41.951,3.038],13);
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      {attribution:'Esri World Imagery — reference basemap',maxZoom:18}).addTo(map);
    markers=L.layerGroup().addTo(map);routes=L.layerGroup().addTo(map);fire=L.layerGroup().addTo(map);
  }
  function select(id) {selectedId=id;render(current);}
  function contact(id) {return [...current.contacts.ranked,...current.contacts.review].find(c=>c.asset_id===id);}
  function facts(id) {return (current.calls||[]).filter(c=>c.asset_id===id);}
  function suppliedRoutes() {
    return (current.plan?.locations||[]).flatMap(location=>(location.routes||[]).map(r=>({...r,asset_id:location.asset_id})));
  }
  function renderMap() {
    if(!map) return;
    markers.clearLayers();routes.clearLayers();fire.clearLayers();
    if(current.fire_geometry) L.geoJSON(current.fire_geometry,{color:'#ff5a3c'}).addTo(fire);
    for(const a of current.assets) {
      if(!Number.isFinite(a.latitude)||!Number.isFinite(a.longitude)) continue;
      const c=contact(a.asset_id);
      const color=c?.status==='window_exhausted'?'#c73a2f':c?.rank?'#d98a1f':'#7a8a7d';
      const marker=L.circleMarker([a.latitude,a.longitude],{radius:8,color:'#fff',weight:1.5,fillColor:color,fillOpacity:0.95});
      marker.assetId=a.asset_id;
      const tooltip=el('span');tooltip.append(el('b',a.name),document.createElement('br'),
        document.createTextNode(`Contact rank ${value(c?.rank)} · ${value(c?.status)}`));
      marker.bindTooltip(tooltip).on('click',event=>select(event.target.assetId)).addTo(markers);
    }
    for(const route of suppliedRoutes()) {
      if(Array.isArray(route.path) && route.path.length>1 && route.path.every(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite))) {
        L.polyline(route.path,{color:'#244a75',dashArray:route.status==='confirmed'?null:'6 6'}).addTo(routes);
      } else if(route.geometry) L.geoJSON(route.geometry,{color:'#244a75'}).addTo(routes);
    }
  }
  function rowFor(c,index,review=false) {
    const a=current.assets.find(a=>a.asset_id===c.asset_id);
    const row=el('div',undefined,review?'qrow':'row'+(selectedId===c.asset_id?' selected':''));
    row.dataset.assetId=c.asset_id;row.addEventListener('click',()=>select(c.asset_id));
    const info=el('div',undefined,'info');info.append(el('div',a?.name||c.name||c.asset_id,'name'),
      el('div',review?(c.review_reasons||[]).join(', '):`${value(c.status)} · slack ${value(c.slack_min)} min`,'sub'));
    row.append(el('div',review?'?':c.rank??index+1,'rank'),info);
    return row;
  }
  function renderDetail() {
    const panel=$('detailPanel');panel.replaceChildren();
    const a=current.assets.find(a=>a.asset_id===selectedId);
    if(!a) {selectedId=null;panel.append(el('p','Select a location from the list, or a pin on the map.'));return;}
    panel.append(el('h3',a.name),el('div',`${value(a.asset_type)} · asset_id ${a.asset_id}`,'kind'));
    const c=contact(a.asset_id);
    panel.append(kv([['Backend contact rank',c?.rank],['Contact status',c?.status],['Slack (minutes)',c?.slack_min],
      ['Time to impact (minutes)',c?.time_to_impact_min],['Policy',c?.policy],
      ['capacity',a.capacity],['estimated occupancy',a.estimated_occupancy],['occupancy basis',a.occupancy_basis],
      ['distance to fire (m)',a.distance_to_fire_m],['forecast source',c?.forecast_source||a.forecast_source],
      ['evacuation source',c?.evacuation_source||a.evacuation_source]]));
    if(c?.review_reasons?.length) panel.append(el('div',`Needs review: ${c.review_reasons.join(', ')}`,'reviewBox'));
    panel.append(el('div','Calls & independent outcomes','panelHead'));
    const calls=facts(a.asset_id);
    if(!calls.length) panel.append(el('p','No call facts supplied. Acknowledgement, assistance, departure and arrival are unknown.'));
    for(const call of calls) {
      panel.append(kv([['Request',call.request_id],['Call status',call.status],
        ['Assistance requested',flag(call.reported_needs_assistance??call.needs_assistance)],['Human requested',flag(call.wants_human)],
        ['Message acknowledged',flag(call.message_acknowledged??call.acknowledged)],
        ['Departure confirmed',flag(call.departure_confirmed)],['Arrival confirmed',flag(call.arrival_confirmed)],
        ['Can self evacuate',flag(call.can_self_evacuate)],['Transport available',flag(call.transport_available)],
        ['Human follow-up required',flag(call.human_followup_required)],['Observed at',time(call.observed_at)],['Source',call.provenance?.source||call.source]]));
    }
    panel.append(el('div','Evidence & provenance','panelHead'));
    for(const source of a.sources||[]) panel.append(el('div',
      `${(source.fields||[]).join(', ')} — ${value(source.source)} · ${time(source.observed_at)}${source.notes?' · '+source.notes:''}`,'sourceItem'));
    if(!a.sources?.length) panel.append(el('p','No recorded sources yet.'));
    panel.append(el('div','Backend plan & tasks','panelHead'));
    const planned=(current.plan?.locations||[]).filter(x=>x.asset_id===a.asset_id);
    for(const location of planned) panel.append(kv([['Plan status',location.status],['Destination',location.destination_id||location.centre_id]]));
    for(const route of suppliedRoutes().filter(x=>x.asset_id===a.asset_id)) panel.append(kv([
      ['Supplied route',route.route_id],['Route status',route.status],['Route provenance',route.source]]));
    for(const task of (current.tasks||[]).filter(t=>t.asset_id===a.asset_id)) panel.append(kv([
      ['Task',task.action||task.kind],['Task status',task.status||task.state],['Assigned team',task.team_id||task.assigned_team_id]]));
    panel.append(el('p','Read-only view. Calls require explicit approval through the shared call queue. No route or movement is confirmed by this screen.'));
  }
  function renderEscalation() {
    const calls=current.calls||[];
    $('escConfirmed').textContent=calls.filter(c=>(c.message_acknowledged??c.acknowledged)===true).length;
    $('escOpen').textContent=calls.filter(c=>c.wants_human===true).length;
    $('escMobility').textContent=calls.filter(c=>(c.reported_needs_assistance??c.needs_assistance)===true).length;
    for(const [id,predicate] of [['escOpenList',c=>c.wants_human===true],['escMobilityList',c=>(c.reported_needs_assistance??c.needs_assistance)===true]]) {
      $(id).replaceChildren();
      for(const call of calls.filter(predicate)) {
        const a=current.assets.find(a=>a.asset_id===call.asset_id),row=el('div',a?.name||call.asset_id,'escRow');
        row.dataset.assetId=call.asset_id;row.addEventListener('click',()=>select(call.asset_id));$(id).append(row);
      }
    }
  }
  function renderDb() {
    $('dbTableBody').replaceChildren();
    for(const a of current.assets) {
      const c=contact(a.asset_id),row=el('tr');
      for(const v of [a.name,a.asset_type,a.distance_to_fire_m,a.capacity,a.estimated_occupancy,c?.status,c?.rank]) row.append(el('td',value(v)));
      row.addEventListener('click',()=>{hideDb();select(a.asset_id);});$('dbTableBody').append(row);
    }
  }
  function render(state) {
    current=state;
    $('modeBadge').textContent=state.input_mode==='offline_demo'?'OFFLINE DEMO — illustrative':`Mode: ${value(state.input_mode)}`;
    $('asOf').textContent=time(state.as_of);$('computedAt').textContent=`${value(state.scenario_id)} / revision ${state.revision}`;
    $('rankedList').replaceChildren(...state.contacts.ranked.map((c,i)=>rowFor(c,i)));
    $('reviewQueue').replaceChildren(el('div',`Needs-review queue (${state.contacts.review.length})`,'panelHead'),
      ...state.contacts.review.map((c,i)=>rowFor(c,i,true)));
    $('mAct').textContent=state.contacts.ranked.filter(c=>c.status==='window_exhausted').length;
    $('mPrep').textContent=state.contacts.ranked.filter(c=>c.status==='window_open').length;
    $('mReview').textContent=state.contacts.review.length;
    $('changeLog').replaceChildren(...(state.events||[]).map(e=>el('div',`${time(e.as_of)} · ${value(e.kind)} · ${value(e.notes)}`,'cl-entry')));
    $('logCount').textContent=(state.events||[]).length;
    $('systemErrors').textContent=state.errors?.length?`System errors: ${state.errors.map(e=>value(e.code)).join(', ')}`:'No system errors reported';
    renderDetail();renderEscalation();renderMap();if(dbOpen) renderDb();
  }
  function status(s) {
    $('statusBadge').textContent=`${s.connection}${s.stale?' · source stale':''}${s.errors?' · system errors':''}`;
    $('statusBadge').className='badge '+(s.connection==='unavailable'?'unavailable':s.stale||s.connection!=='connected'?'stale':'current');
  }
  function hideDb() {dbOpen=false;$('dbView').classList.remove('open');$('main').style.display='flex';map?.invalidateSize();}
  $('openDbBtn').addEventListener('click',()=>{dbOpen=true;$('dbView').classList.add('open');$('main').style.display='none';if(current) renderDb();});
  $('backToMapBtn').addEventListener('click',hideDb);
  $('changeLogBtn').addEventListener('click',()=>$('changeLog').classList.toggle('open'));
  $('toggle3dBtn').addEventListener('click',()=>{$('map').classList.toggle('tilt3d');$('toggle3dBtn').classList.toggle('on');map?.invalidateSize();});
  initMap();
  root.DashboardView={render,status};
  if(root.DashboardClient) {
    const client=new root.DashboardClient({
      fetchState:async()=>{const r=await fetch('/api/state',{cache:'no-store'});if(!r.ok) throw new Error('unavailable');return r.json();},
      WebSocket:root.WebSocket,socketUrl:`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/updates`,
      onState:render,onStatus:status});
    $('nextUpdateBtn').addEventListener('click',()=>{client.stop();client.start();});
    root.addEventListener('pagehide',()=>client.stop());client.start();
  }
})(globalThis);
