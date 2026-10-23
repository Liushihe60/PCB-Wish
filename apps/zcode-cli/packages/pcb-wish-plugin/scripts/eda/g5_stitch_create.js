const tree = await eda.dmt_EditorControl.getSplitScreenTree();
const tabs = tree.tabs || [];
const pcb = tabs.find(t => t.tabId && t.tabId.indexOf('1b70bcd67c184b71@') === 0);
await eda.dmt_EditorControl.activateDocument(pcb.tabId);
const list = __CLI__.args.vias;
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
