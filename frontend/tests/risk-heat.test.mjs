import test from "node:test";
import assert from "node:assert/strict";
import { riskHeatPoints, riskHeatColour } from "../src/state/risk-heat.mjs";
test("risk heat uses only supplied bounded scores and valid GPS, retaining zero", () => {
 const asset = { latitude: 41, longitude: 3, risk_score: 0 };
 const assets = [asset, {...asset,risk_score:100}, ...[null,undefined,-1,101,"82",NaN].map(risk_score=>({...asset,risk_score})), {...asset,latitude:91}, {...asset,longitude:181}, {...asset,latitude:null}];
 assert.deepEqual(riskHeatPoints([{assets}]), [{latitude:41,longitude:3,score:0},{latitude:41,longitude:3,score:100}]);
 assert.deepEqual(riskHeatPoints([{}]),[]);
});
test("heat scale endpoints and midpoint match rendered legend", () => {
 assert.deepEqual(riskHeatColour(0),[36,109,186]);
 assert.deepEqual(riskHeatColour(50),[249,199,79]);
 assert.deepEqual(riskHeatColour(100),[194,34,37]);
});
