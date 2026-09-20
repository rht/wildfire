import test from 'node:test';
import assert from 'node:assert/strict';
import { validateCrewPreview } from '../src/state/crew-order-api.mjs';
const identity = {source:'connected',incident_id:'incident-a',snapshot_id:'snapshot-a',revision:3,team_id:'crew-a',plan_version:'base-version'};
const ids=['second','first'];
const preview=()=>({...identity,review_version:'review-version',can_confirm:true,blockers:[],reviewed_plan:{team_id:'crew-a',action_ids:ids,tasks:[{action_id:'second'},{action_id:'first'}]}});
test('accepts the server preview only for the exact displayed context and requested order',()=>{
 const data=preview();assert.equal(validateCrewPreview(data,identity,ids),data);
 for(const key of Object.keys(identity))assert.throws(()=>validateCrewPreview({...data,[key]:'different'},identity,ids),/changed/);
 assert.throws(()=>validateCrewPreview(data,identity,[...ids].reverse()),/changed/);
});
test('rejects missing version, validation result, blockers and mismatched task order',()=>{
 for(const changes of [{review_version:null},{can_confirm:null},{blockers:null},{reviewed_plan:{...preview().reviewed_plan,tasks:[{action_id:'first'},{action_id:'second'}]}},{reviewed_plan:{...preview().reviewed_plan,team_id:'other'}}]){
  assert.throws(()=>validateCrewPreview({...preview(),...changes},identity,ids),/changed/);
 }
});
test('keeps an infeasible preview and its exact blockers for display without treating it as approvable',()=>{
 const data={...preview(),can_confirm:false,blockers:[{code:'deadline',action_id:'first'}]};
 assert.equal(validateCrewPreview(data,identity,ids).can_confirm,false);
 assert.deepEqual(validateCrewPreview(data,identity,ids).blockers,data.blockers);
});
