import test from 'node:test';
import assert from 'node:assert/strict';
import { assistanceTotals, locationPriority, resourcePriority, priorityList } from '../src/state/priority.mjs';
import { toIncident } from '../src/state/model.mjs';
test('assistance totals count locations once and keep disputed needs pending',()=>{
 const i=toIncident({assets:['a','b','c','d'].map(asset_id=>({asset_id})),calls:[{asset_id:'a',reported_needs_assistance:true},{asset_id:'a',reported_needs_assistance:true},{asset_id:'b',reported_needs_assistance:true},{asset_id:'b',reported_needs_assistance:false}],plan:{locations:[{asset_id:'c',assistance_review_required:true},{asset_id:'d',reported_needs_assistance:false}]}});
 assert.deepEqual(assistanceTotals([i]),{total:3,confirmed:1,pending:2});
});
test('priority lists sort finite windows first and preserve unknowns without an invented rank',()=>{
 const i=toIncident({contacts:{ranked:[{asset_id:'a',slack_min:15},{asset_id:'b',slack_min:-2}]}});
 const rows=priorityList(['unknown','a','b'].map(id=>({id,priority:locationPriority(i,id)})));
 assert.deepEqual(rows.map(r=>r.id),['b','a','unknown']);
 assert.deepEqual(rows.map(r=>r.priorityRank),[1,2,null]);
 assert.match(rows[0].priority.label,/exhausted/);
});
test('resource priority uses proposed finish margin, excludes completed tasks and exposes missing timing',()=>{
 assert.equal(resourcePriority({tasks:[{deadline_min:10,finish_min:12,status:'proposed'},{deadline_min:1,finish_min:20,status:'completed'}]}).order,-2);
 assert.equal(resourcePriority({tasks:[{deadline_min:3}]}).order,Infinity);
 assert.equal(resourcePriority(null).label,'No plan supplied');
});

test('shared locations count once across incidents, conflicting needs remain pending',()=>{
 const incident = needs => toIncident({assets:[{asset_id:'osm:1'}], plan:{locations:[{asset_id:'osm:1',reported_needs_assistance:needs}]}});
 assert.deepEqual(assistanceTotals([incident(true),incident(true)]),{total:1,confirmed:1,pending:0});
 assert.deepEqual(assistanceTotals([incident(true),incident(false)]),{total:1,confirmed:0,pending:1});
});
