const tree = await eda.dmt_EditorControl.getSplitScreenTree();
const tabs = tree.tabs || [];
const pcb = tabs.find(t => t.tabId && t.tabId.indexOf('1b70bcd67c184b71@') === 0);
await eda.dmt_EditorControl.activateDocument(pcb.tabId);
const list = [{"x": 39.37, "y": 39.37}, {"x": 374.02, "y": 39.37}, {"x": 629.92, "y": 39.37}, {"x": 885.83, "y": 39.37}, {"x": 1318.9, "y": 39.37}, {"x": 1574.8, "y": 39.37}, {"x": 1732.64, "y": 58.7}, {"x": 1732.64, "y": 255.55}, {"x": 1732.64, "y": 727.99}, {"x": 1732.64, "y": 983.9}, {"x": 1713.19, "y": 1141.61}, {"x": 1457.28, "y": 1141.61}, {"x": 1201.38, "y": 1141.61}, {"x": 945.47, "y": 1141.61}, {"x": 256.5, "y": 1141.61}, {"x": 39.96, "y": 1141.61}, {"x": 39.37, "y": 965.04}, {"x": 39.37, "y": 728.82}, {"x": 39.37, "y": 433.54}];
const out = {made: [], errs: []};
for (const v of list) {
  try {
    const via = await eda.pcb_PrimitiveVia.create('GND', v.x, v.y, 12, 24);
    out.made.push({x: v.x, y: v.y, id: via ? via.getState_PrimitiveId() : null});
  } catch(e) { out.errs.push({x: v.x, y: v.y, err: String(e).slice(0,150)}); }
}
out.counts = {lines: (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length,
  vias: (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length,
  comps: (await eda.pcb_PrimitiveComponent.getAllPrimitiveId()).length};
return out;
