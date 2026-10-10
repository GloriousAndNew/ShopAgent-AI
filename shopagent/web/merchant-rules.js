let policies=[], promotions=[], policyStarters=[], policyRevision=0, promotionRevision=0, rulesBusy=false;
// 表单时间统一解释为北京时间，避免浏览器时区不同改变活动有效期。
const beijingInput=(iso)=>iso?new Date(new Date(iso).getTime()+8*3600000).toISOString().slice(0,16):'';
const toISO=(value)=>value?new Date(value+(value.length===16?':00':'')+'+08:00').toISOString():null;
function liveLabel(p){if(!p.published)return'未发布 / 已停用';if(p.effective)return'当前有效';return new Date(p.published.starts_at).getTime()>Date.now()?'尚未开始':'已过期';}
function rulesText(id,message){$(id).textContent=message;}
async function ruleAction(path,body,messageID,refresh){if(rulesBusy)return;rulesBusy=true;try{await api(path,body);rulesText(messageID,body.action==='publish'?'已发布。是否立即生效取决于有效期；请核对当前版本。':'已停用，后续客服查询不再提供此内容。');await refresh();}catch(e){rulesText(messageID,e.message);}finally{rulesBusy=false;}}

$('open-policies').onclick=async()=>{if(busy||user?.role!=='admin')return;selectView('policies','政策管理');await loadPolicies();if(productionMode)return;try{policyStarters=(await api('/admin/policy-starter')).drafts;$('policy-starter-select').replaceChildren();for(const p of policyStarters){const option=el('option',p.title);option.value=p.policy_id;$('policy-starter-select').append(option);}}catch(e){rulesText('policy-message',e.message);}};
$('policy-back').onclick=()=>{if(!rulesBusy)showAdmin();};
$('promotion-back').onclick=()=>{if(!rulesBusy)showAdmin();};
$('refresh-policies').onclick=loadPolicies;
$('refresh-promotions').onclick=loadPromotions;

async function loadPolicies(){try{
  policies=(await api('/admin/policies')).policies;$('policy-list').replaceChildren();
  for(const p of policies){const c=el('article',undefined,'catalog-item');c.append(el('span',`${p.id} · ${liveLabel(p)}`,'tag'),el('h3',p.draft.title),el('p',`草稿版本 ${p.revision} · 发布版本 ${p.published?.version??'无'}`,'muted'));
    if(p.published?.rule?.legacy)c.append(el('p','旧演示退货规则仍在使用，请载入并核实新草案后发布。','rule-note'));
    const preview=el('details');preview.append(el('summary','查看当前发布内容'),el('div',p.published?.text??'尚未发布；草稿不会提供给顾客。','policy-content'));c.append(preview);
    const actions=el('div',undefined,'catalog-toolbar'),edit=el('button','编辑草稿','secondary'),publish=el('button','发布草稿','secondary'),disable=el('button','停用','secondary'),history=el('button','历史版本','secondary');
    edit.onclick=()=>{if(!rulesBusy)openPolicy(p.draft,p.revision);};publish.onclick=()=>ruleAction('/admin/policies/action',{policy_id:p.id,expected_revision:p.revision,action:'publish'},'policy-message',loadPolicies);disable.disabled=!p.published;disable.onclick=()=>ruleAction('/admin/policies/action',{policy_id:p.id,expected_revision:p.revision,action:'disable'},'policy-message',loadPolicies);
    history.onclick=async()=>{try{const data=await api(`/admin/policies/${encodeURIComponent(p.id)}/history`);$('policy-history-list').replaceChildren();for(const v of data.versions){const d=el('details');d.append(el('summary',`${p.id} · ${v.version} · ${time(v.created_at)}`),el('div',v.document.text,'policy-content'));$('policy-history-list').append(d);}if(!data.versions.length)$('policy-history-list').append(el('p','暂无发布历史。'));$('policy-history').classList.remove('hidden');$('policy-history').scrollIntoView({behavior:'smooth'});}catch(e){rulesText('policy-message',e.message);}};
    actions.append(edit,publish,disable,history);c.append(actions);$('policy-list').append(c);
  }
}catch(e){rulesText('policy-message',e.message);}}

function showReturnFields(){$('return-rule-editor').classList.toggle('hidden',$('k-id').value.trim()!=='return');}
$('k-id').oninput=showReturnFields;
function openPolicy(p={},revision=0){policyRevision=revision;for(const[id,key]of[['k-id','policy_id'],['k-title','title'],['k-tags','tags'],['k-text','text']])$(id).value=p[key]??'';
  $('k-id').readOnly=revision>0;$('k-start').value=beijingInput(p.starts_at);$('k-end').value=beijingInput(p.ends_at);$('k-simulation').checked=p.simulation??true;
  const rule=p.return_rule??{};$('k-days').value=rule.days??7;$('k-next-day').checked=rule.start_next_day??true;$('k-opened').checked=rule.opened_requires_review??true;$('k-quality').checked=rule.quality_manual_review??true;
  rulesText('policy-editor-error','');showReturnFields();$('policy-editor').classList.remove('hidden');$('policy-editor').scrollIntoView({behavior:'smooth'});
}
$('new-policy').onclick=()=>{if(!rulesBusy)openPolicy();};
$('load-policy-starter').onclick=()=>{if(rulesBusy)return;const starter=policyStarters.find(p=>p.policy_id===$('policy-starter-select').value);if(!starter)return;const existing=policies.find(p=>p.id===starter.policy_id);openPolicy(starter,existing?.revision??0);rulesText('policy-message','已载入表单，尚未保存或发布。原有发布内容保持不变。');};
$('cancel-policy').onclick=()=>{if(!rulesBusy)$('policy-editor').classList.add('hidden');};
$('policy-form').onsubmit=async(e)=>{e.preventDefault();if(rulesBusy)return;rulesBusy=true;$('save-policy').disabled=true;
  try{const id=$('k-id').value.trim(),body={policy_id:id,expected_revision:policyRevision,title:$('k-title').value.trim(),tags:$('k-tags').value.trim(),text:$('k-text').value.trim(),kind:id==='return'?'return':'general',return_rule:id==='return'?{days:Number($('k-days').value),start_next_day:$('k-next-day').checked,opened_requires_review:$('k-opened').checked,quality_manual_review:$('k-quality').checked}:null,starts_at:toISO($('k-start').value),ends_at:toISO($('k-end').value),simulation:$('k-simulation').checked};
    await api('/admin/policies/save',body);$('policy-editor').classList.add('hidden');rulesText('policy-message','草稿已保存，尚未发布。核实文字和执行规则后，再点击发布草稿。');await loadPolicies();
  }catch(e){rulesText('policy-editor-error',e.message);}finally{rulesBusy=false;$('save-policy').disabled=false;}
};

$('open-promotions').onclick=async()=>{if(busy||user?.role!=='admin')return;selectView('promotions','优惠管理');await loadPromotions();};
async function loadPromotions(){try{
  promotions=(await api('/admin/promotions')).promotions;$('promotion-list').replaceChildren();
  if(!promotions.length)$('promotion-list').append(el('p','暂无活动，可先新建草稿。'));
  for(const p of promotions){const c=el('article',undefined,'catalog-item'),d=p.published??p.draft;c.append(el('span',`${p.id} · ${liveLabel(p)}`,'tag'),el('h3',p.draft.title),el('p',`${d.kind==='percentage'?`${d.rate_percent}%原价`:`减${money(d.discount_fen)}`} · 原价小计至少${money(d.min_amount_fen)} · 不叠加`),el('p',`分类 ${d.category||'不限'} · 编号 ${d.product_ids.join('、')||'不限'}`,'muted'),el('p',`北京时间 ${beijingInput(d.starts_at)||'未填'} 至 ${beijingInput(d.ends_at)||'未填'}`,'muted'),el('p',`发布版本 ${p.published?.version??'无'} · ${d.simulation?'示例活动':'商家活动试算'}`,'muted'));
    const row=el('div',undefined,'catalog-toolbar'),edit=el('button','编辑草稿','secondary'),publish=el('button','发布活动','secondary'),disable=el('button','停用','secondary');edit.onclick=()=>{if(!rulesBusy)openPromotion(p.draft,p.revision);};publish.onclick=()=>ruleAction('/admin/promotions/action',{promotion_id:p.id,expected_revision:p.revision,action:'publish'},'promotion-message',loadPromotions);disable.disabled=!p.published;disable.onclick=()=>ruleAction('/admin/promotions/action',{promotion_id:p.id,expected_revision:p.revision,action:'disable'},'promotion-message',loadPromotions);row.append(edit,publish,disable);c.append(row);$('promotion-list').append(c);
  }
}catch(e){rulesText('promotion-message',e.message);}}
function toggleDiscount(){$('fixed-label').classList.toggle('hidden',$('m-kind').value!=='fixed');$('rate-label').classList.toggle('hidden',$('m-kind').value==='fixed');}
$('m-kind').onchange=toggleDiscount;
function openPromotion(p={},revision=0){promotionRevision=revision;for(const[id,key]of[['m-id','promotion_id'],['m-title','title'],['m-category','category']])$(id).value=p[key]??'';$('m-id').readOnly=revision>0;$('m-products').value=p.product_ids?.join('、')??'';$('m-kind').value=p.kind??'percentage';$('m-rate').value=p.rate_percent??90;$('m-min').value=((p.min_amount_fen??0)/100).toFixed(2);$('m-discount').value=p.discount_fen?(p.discount_fen/100).toFixed(2):'';$('m-start').value=beijingInput(p.starts_at);$('m-end').value=beijingInput(p.ends_at);$('m-simulation').checked=p.simulation??true;toggleDiscount();rulesText('promotion-editor-error','');$('promotion-editor').classList.remove('hidden');$('promotion-editor').scrollIntoView({behavior:'smooth'});}
$('new-promotion').onclick=()=>{if(!rulesBusy)openPromotion();};
$('cancel-promotion').onclick=()=>{if(!rulesBusy)$('promotion-editor').classList.add('hidden');};
$('promotion-form').onsubmit=async(e)=>{e.preventDefault();if(rulesBusy)return;rulesBusy=true;$('save-promotion').disabled=true;
  try{const kind=$('m-kind').value,minimum=$('m-min').value;const body={promotion_id:$('m-id').value.trim(),expected_revision:promotionRevision,title:$('m-title').value.trim(),category:$('m-category').value.trim(),product_ids:$('m-products').value.split(/[、,，\s]+/).filter(Boolean),kind,rate_percent:kind==='percentage'?Number($('m-rate').value):100,discount_fen:kind==='fixed'?priceInFen($('m-discount').value):0,min_amount_fen:/^0(?:\.0{1,2})?$/.test(minimum)?0:priceInFen(minimum),starts_at:toISO($('m-start').value),ends_at:toISO($('m-end').value),simulation:$('m-simulation').checked};
    await api('/admin/promotions/save',body);$('promotion-editor').classList.add('hidden');rulesText('promotion-message','活动草稿已保存；尚未发布，原有发布版本保持不变。');await loadPromotions();
  }catch(e){rulesText('promotion-editor-error',e.message);}finally{rulesBusy=false;$('save-promotion').disabled=false;}
};
