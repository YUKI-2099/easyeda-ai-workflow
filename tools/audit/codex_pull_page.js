const d = await eda.dmt_SelectControl.getCurrentDocumentInfo();
if ((PROJECT_UUID && d.parentProjectUuid !== PROJECT_UUID) || d.uuid !== TARGET_PAGE) throw new Error('scope drift');
const cs = await eda.sch_PrimitiveComponent.getAll();
const ws = await eda.sch_PrimitiveWire.getAll();
const result = {document:d, components:[], wires:[]};
for (const c of cs) {
  const row={id:c.getState_PrimitiveId(),type:c.getState_ComponentType(),ref:c.getState_Designator(),x:c.getState_X(),y:c.getState_Y(),net:c.getState_Net(),properties:c.getState_OtherProperty(),supplier:c.getState_SupplierId(),device:c.getState_Component(),pins:[]};
  if (row.ref) {
    const ps=await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(row.id);
    if (!ps) throw new Error('pins missing '+row.ref);
    row.pins=ps.map(p=>({number:p.getState_PinNumber(),name:p.getState_PinName(),x:p.getState_X(),y:p.getState_Y(),rotation:p.getState_Rotation(),length:p.getState_PinLength(),nc:p.getState_NoConnected()}));
  }
  result.components.push(row);
}
for (const w of ws) result.wires.push({id:w.getState_PrimitiveId(),net:w.getState_Net(),line:w.getState_Line()});
return result;
