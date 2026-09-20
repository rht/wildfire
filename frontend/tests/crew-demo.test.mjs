import test from 'node:test';
import assert from 'node:assert/strict';
import { demoIncidents } from '../src/state/demo.mjs';
test('demo review has explicit sourced start, qualified directed legs and action inputs for recalculation',()=>{
 for(const incident of demoIncidents)for(const team of incident.plan.response.teams){
  const context=team.planning_context;
  assert.ok(context?.start?.source,'explicit starting point');
  let node=context.start.node_id;
  for(const task of team.tasks){
   assert.equal(task.from_node,node);
   const leg=context.routes.find(r=>r.from_node===node&&r.to_node===task.to_node);
   assert.ok(leg?.confirmed&&leg.safe);
   assert.deepEqual(task.path_lonlat,leg.path_lonlat);
   assert.equal(task.duration_min,task.finish_min-task.start_min);
   assert.equal(task.readiness_required,false);
   node=task.to_node;
  }
  const [first,second]=team.tasks;
  assert.ok(context.routes.some(r=>r.from_node===context.start.node_id&&r.to_node===second.to_node));
  assert.ok(context.routes.some(r=>r.from_node===second.to_node&&r.to_node===first.to_node));
 }
});
