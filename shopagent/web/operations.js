// 公开资料只以纯文本展示，调用额度仅管理员可见。
let operationsRevision=null;
function applyStorefront(value){
  if(!value||entryPath!=='/customer')return;
  document.querySelector('.login-card h1').textContent=value.shop_name+' · 客服';
  $('welcome').querySelector('h1,h2').textContent=value.greeting;
  let loginHours=$('store-hours-login');
  if(!loginHours){loginHours=el('p',undefined,'small');loginHours.id='store-hours-login';$('customer-start').prepend(loginHours);}
  loginHours.textContent=value.service_hours;
  let chatHours=$('store-hours-chat');
  if(!chatHours){chatHours=el('p',undefined,'small');chatHours.id='store-hours-chat';$('welcome').append(chatHours);}
  chatHours.textContent=value.service_hours;
}
async function loadOperations(){
  if(user?.role!=='admin')return;
  try{
    const value=await api('/admin/operations');operationsRevision=value.revision;
    $('op-name').value=value.shop_name;$('op-greeting').value=value.greeting;$('op-hours').value=value.service_hours;
    $('op-limit').value=value.daily_model_calls;$('op-enabled').checked=value.ai_enabled;
    $('op-usage').textContent=`${value.day}（北京时间）已调用 ${value.model_calls_used} 次，剩余 ${value.model_calls_remaining} 次。${value.ai_enabled?'已允许真实模型调用。':'真实模型调用已暂停。'}`;
  }catch(e){$('op-status').textContent=e.message;}
}
$('op-refresh').onclick=()=>{ $('op-status').textContent='';loadOperations(); };
$('operations-form').onsubmit=async event=>{
  event.preventDefault();if(operationsRevision===null)return;
  $('op-save').disabled=true;$('op-status').textContent='';
  try{
    await api('/admin/operations',{expected_revision:operationsRevision,shop_name:$('op-name').value,greeting:$('op-greeting').value,service_hours:$('op-hours').value,ai_enabled:$('op-enabled').checked,daily_model_calls:Number($('op-limit').value)});
    await loadOperations();await refreshRuntime();$('op-status').textContent='已保存；顾客重新打开或登录入口时显示最新信息。';
  }catch(e){$('op-status').textContent=e.message;}finally{$('op-save').disabled=false;}
};
