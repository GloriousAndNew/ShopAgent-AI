// 商品后台只调用管理员API；工具进程仍独立校验角色。
let catalogProducts = [], editingRevision = 0, importPayload = null, skuSequence = 0, catalogWorking = false;
const productStatusName = {draft:'草稿',published:'已上架',archived:'已下架'};

function catalogMessage(message){$('catalog-status').textContent=message;}
function productInput(id,value){$(id).value=value??'';}

async function showProducts(){
  if(busy||user?.role!=='admin')return;
  selectView('products','商品管理');
  await refreshProducts();
}
$('open-products').onclick=showProducts;
$('back-admin').onclick=()=>{if(!catalogWorking)showAdmin();};
$('product-filter').onsubmit=async(e)=>{e.preventDefault();await refreshProducts();};

async function refreshProducts(){
  try{
    const query=new URLSearchParams({keyword:$('catalog-keyword').value.trim(),status:$('catalog-filter').value});
    const data=await api('/admin/products?'+query);
    catalogProducts=data.products;
    $('catalog-list').replaceChildren();
    if(!catalogProducts.length)$('catalog-list').append(el('p','没有符合条件的商品。可以新增商品或导入草稿。','muted'));
    for(const p of catalogProducts){
      const item=el('article',undefined,'catalog-item');
      item.append(el('span',`${p.id} · ${productStatusName[p.status]}`,'tag'),el('h3',p.name),el('div',money(p.price_fen),'price'),el('p',`${p.category} · ${p.simulation?'示例资料':'商家录入资料'} · 版本 ${p.revision}`,'muted'));
      for(const sku of p.skus)item.append(el('p',`${sku.color}：${sku.stock} 件${sku.stock===0?' · 缺货':''}`,'muted'));
      const actions=el('div',undefined,'catalog-toolbar'),edit=el('button','编辑商品','secondary'),status=el('button',p.status==='published'?'下架':'上架','secondary');
      edit.onclick=()=>{if(!catalogWorking)openProductEditor(p);};
      status.onclick=async()=>{
        if(catalogWorking)return;
        catalogWorking=true;status.disabled=true;
        try{await api('/admin/products/status',{product_id:p.id,expected_revision:p.revision,status:p.status==='published'?'archived':'published'});catalogMessage(`${p.id} 已${p.status==='published'?'下架，后续客服查询不可见':'上架，客服可查询'}。`);await refreshProducts();}
        catch(error){catalogMessage(error.message);}
        finally{catalogWorking=false;status.disabled=false;}
      };
      actions.append(edit,status);item.append(actions);$('catalog-list').append(item);
    }
  }catch(error){catalogMessage(error.message);}
}

function addSKURow(color='',stock=0){
  const key=++skuSequence,row=el('div',undefined,'sku-row');
  const colorLabel=el('label','颜色或规格'),colorInput=el('input'),stockLabel=el('label','库存（件）'),stockInput=el('input'),remove=el('button','移除','secondary');
  colorInput.value=color;colorInput.required=true;colorInput.maxLength=20;colorInput.dataset.field='color';colorInput.setAttribute('aria-label',`颜色 ${key}`);
  stockInput.type='number';stockInput.value=stock;stockInput.min=0;stockInput.max=1000000;stockInput.step=1;stockInput.required=true;stockInput.dataset.field='stock';stockInput.setAttribute('aria-label',`库存 ${key}`);
  remove.type='button';remove.onclick=()=>{if($('sku-editor').children.length<=1){$('editor-error').textContent='至少保留一种颜色或规格。';return;}row.remove();};
  colorLabel.append(colorInput);stockLabel.append(stockInput);row.append(colorLabel,stockLabel,remove);$('sku-editor').append(row);
}

function openProductEditor(p=null){
  editingRevision=p?.revision??0;
  $('editor-title').textContent=p?'编辑商品 · '+p.id:'新增商品';
  productInput('p-id',p?.id);$('p-id').readOnly=!!p;
  for(const [id,key] of [['p-name','name'],['p-category','category'],['p-description','description'],['p-specification','specification'],['p-brand','brand'],['p-warranty','warranty_months']])productInput(id,p?.[key]);
  productInput('p-price',p?(p.price_fen/100).toFixed(2):'');
  productInput('p-unknown',p?.unknown_fields?.join('、'));
  $('p-status').value=p?.status??'draft';$('p-noise').checked=!!p?.noise_cancel;$('p-simulation').checked=p?.simulation??true;
  $('sku-editor').replaceChildren();skuSequence=0;for(const sku of p?.skus??[{color:'黑色',stock:0},{color:'白色',stock:0}])addSKURow(sku.color,sku.stock);
  $('editor-error').textContent='';$('product-editor').classList.remove('hidden');$('product-editor').scrollIntoView({behavior:'smooth',block:'start'});
}
$('new-product').onclick=()=>{if(!catalogWorking)openProductEditor();};
$('add-sku').onclick=()=>{if($('sku-editor').children.length>=20){$('editor-error').textContent='最多20种颜色或规格。';return;}addSKURow();};
$('cancel-edit').onclick=()=>{if(!catalogWorking)$('product-editor').classList.add('hidden');};

function priceInFen(raw){
  if(!/^\d+(\.\d{1,2})?$/.test(raw))throw new Error('售价必须是正数，最多两位小数。');
  const [whole,fraction='']=raw.split('.');const value=Number(whole)*100+Number(fraction.padEnd(2,'0'));
  if(!Number.isSafeInteger(value)||value<1||value>10000000)throw new Error('售价应在0.01元至100000元之间。');
  return value;
}
$('product-form').onsubmit=async(e)=>{
  e.preventDefault();if(catalogWorking)return;
  $('editor-error').textContent='';
  try{
    const skus=[...$('sku-editor').children].map(row=>({color:row.querySelector('[data-field=color]').value.trim(),stock:Number(row.querySelector('[data-field=stock]').value)}));
    const payload={product_id:$('p-id').value.trim(),expected_revision:editingRevision,name:$('p-name').value.trim(),category:$('p-category').value.trim(),price_fen:priceInFen($('p-price').value),noise_cancel:$('p-noise').checked,
      description:$('p-description').value.trim(),specification:$('p-specification').value.trim(),brand:$('p-brand').value.trim(),unknown_fields:$('p-unknown').value.split(/[、,，]/).map(s=>s.trim()).filter(Boolean),
      warranty_months:$('p-warranty').value===''?null:Number($('p-warranty').value),status:$('p-status').value,simulation:$('p-simulation').checked,skus};
    catalogWorking=true;$('save-product').disabled=true;
    const saved=await api('/admin/products/save',payload);
    $('product-editor').classList.add('hidden');catalogMessage(`${saved.id} 保存成功 · ${productStatusName[saved.status]}。${saved.status==='published'?'新的客服查询使用更新后的资料。':'顾客暂不可见，审核后可上架。'}`);
    await refreshProducts();
  }catch(error){$('editor-error').textContent=error.message;}
  finally{catalogWorking=false;$('save-product').disabled=false;}
};

function normalizeImport(data){
  if(!data||!Array.isArray(data.products))throw new Error('文件需要包含products商品数组。');
  if(data.products.length<1||data.products.length>100)throw new Error('每次导入1至100款商品。');
  const keys=['name','category','price_fen','noise_cancel','description','specification','brand','unknown_fields','warranty_months','simulation'];
  return {products:data.products.map(p=>{
    if(!p||typeof p!=='object')throw new Error('每行必须是商品对象。');
    if(p.product_id)return p;
    const value={product_id:p.id,expected_revision:0,status:'draft',skus:p.skus?.map(s=>({color:s.color,stock:s.stock}))};
    for(const key of keys)if(p[key]!==undefined&&p[key]!==null)value[key]=p[key];
    return value;
  })};
}
async function previewProducts(payload){
  importPayload=null;$('commit-import').disabled=true;
  const preview=await api('/admin/products/import/preview',payload);
  $('import-preview').classList.remove('hidden');$('import-rows').replaceChildren();$('import-notice').textContent=preview.notice;
  for(const error of preview.errors)$('import-rows').append(el('p',`第${error.row}行：${error.message}`,'error'));
  for(const p of preview.products)$('import-rows').append(el('p',`${p.product_id} · ${p.name} · ${money(p.price_fen)} · ${p.skus.length}种颜色 · 草稿`,'preview-row'));
  if(preview.valid){importPayload=payload;$('commit-import').disabled=false;catalogMessage(`预览通过：${preview.count}款。确认后保存为草稿。`);}
  else catalogMessage('预览有错误，未写入任何商品。请修改文件或使用编辑功能。');
  $('import-preview').scrollIntoView({behavior:'smooth',block:'start'});
}
$('starter-import').onclick=async()=>{
  if(catalogWorking)return;catalogWorking=true;$('starter-import').disabled=true;
  try{const starter=await api('/admin/products/starter');await previewProducts({products:starter.products});}catch(error){catalogMessage(error.message);}finally{catalogWorking=false;$('starter-import').disabled=false;}
};
$('open-import').onclick=()=>{if(!catalogWorking)$('import-file').click();};
$('import-file').onchange=async()=>{
  const file=$('import-file').files[0];if(!file)return;
  if(file.size>512*1024){catalogMessage('JSON文件不得超过512KB。');$('import-file').value='';return;}
  try{let data;try{data=JSON.parse(await file.text());}catch{throw new Error('JSON格式不正确，请检查文件。');}await previewProducts(normalizeImport(data));}
  catch(error){catalogMessage(error.message);}finally{$('import-file').value='';}
};
$('cancel-import').onclick=()=>{if(catalogWorking)return;importPayload=null;$('import-preview').classList.add('hidden');};
$('commit-import').onclick=async()=>{
  if(!importPayload||catalogWorking)return;catalogWorking=true;$('commit-import').disabled=true;
  try{const result=await api('/admin/products/import/commit',importPayload);importPayload=null;$('import-preview').classList.add('hidden');catalogMessage(`已导入${result.imported}款草稿。原有商品未被覆盖；审核商品资料后再上架。`);$('catalog-filter').value='draft';$('catalog-keyword').value='';await refreshProducts();}
  catch(error){catalogMessage(error.message+' 未自动重试；请重新预览，核实是否已导入。');importPayload=null;}
  finally{catalogWorking=false;}
};
