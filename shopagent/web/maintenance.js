let privacyVersion=null,retentionRevision=null,retentionEnabled=false;
async function loadPrivacy(){
  try{
    const value=await api('/privacy');privacyVersion=value.version;
    for(const id of ['privacy-login','privacy-chat']){const root=$(id);root.replaceChildren();for(const text of value.paragraphs)root.append(el('p',text));root.append(el('small','说明版本：'+value.version));}
    $('start-guest').disabled=false;
  }catch(e){privacyVersion=null;$('start-guest').disabled=true;$('guest-error').textContent='说明加载失败，请刷新页面后重试。';}
}
const eventLabels={MODEL_TIMEOUT:'模型超时',MODEL_UNAVAILABLE:'模型不可用',MODEL_AUTH_FAILED:'模型认证失败',MODEL_RATE_LIMIT:'模型服务限额',MODEL_INVALID_RESPONSE:'模型格式异常',MODEL_OUTPUT_LIMIT:'模型输出上限',MODEL_DAILY_LIMIT:'每日调用额度用完',AI_PAUSED:'AI 已暂停',AGENT_TIMEOUT:'Agent 协作超时',AGENT_STEP_LIMIT:'本轮协作达到上限',HTTP_UNEXPECTED:'请求异常',MAINTENANCE_FAILED:'自动维护失败',OTHER_BUSINESS_FAILURE:'其他业务查询失败'};
async function loadMaintenance(){
  if(user?.role!=='admin')return;
  try{
    const value=await api('/admin/maintenance'),r=value.retention;retentionRevision=r.revision;retentionEnabled=r.enabled;
    $('retention-days').value=r.days;$('retention-enabled').checked=r.enabled;$('retention-cleanup').disabled=!r.enabled;
    $('retention-preview').textContent=`${r.enabled?'已启用':'未启用'} · 按已保存的 ${r.days} 天期限，下一批可清理 ${r.eligible_sessions} 个会话、${r.expired_tokens} 个过期凭据。${r.last_run?'上次执行：'+time(r.last_run.created_at)+'，删除'+r.last_run.sessions+'个会话。':'尚未执行清理。'}`;
    $('monitor-events').replaceChildren();for(const e of value.monitor.counts)$('monitor-events').append(el('p',`${eventLabels[e.code]||e.code}：${e.count} 次`));
    if(!value.monitor.counts.length)$('monitor-events').append(el('p','最近24小时暂无已记录的运行事件。','muted'));
    for(const e of value.monitor.recent.slice(0,5))$('monitor-events').append(el('small',time(e.created_at)+' · '+(eventLabels[e.code]||e.code),'muted'));
    const faults=value.modules.filter(p=>p.state==='failed');$('monitor-modules').textContent=faults.length?'当前故障模块：'+faults.map(p=>p.id).join('、'):'当前未检测到 failed 状态的模块；停用模块请在技术模块查看。';
  }catch(e){$('maintenance-status').textContent=e.message;}
}
$('maintenance-refresh').onclick=()=>{ $('maintenance-status').textContent='';loadMaintenance(); };
$('retention-form').onsubmit=async event=>{
  event.preventDefault();if(retentionRevision===null)return;
  const enabled=$('retention-enabled').checked,days=Number($('retention-days').value);
  if(enabled&&!confirm(`启用后会自动永久删除满足 ${days} 天期限的访客咨询和已关闭工单，不能在网页撤销；备份和模型服务记录不受此操作影响。确认按这个期限启用吗？`))return;
  $('retention-save').disabled=true;
  try{await api('/admin/retention',{expected_revision:retentionRevision,enabled,days});await loadMaintenance();await loadPrivacy();$('maintenance-status').textContent='已保存。开启时程序每小时检查一次；关闭后不再开始新的自动清理。';}
  catch(e){$('maintenance-status').textContent=e.message;}finally{$('retention-save').disabled=false;}
};
$('retention-cleanup').onclick=async()=>{
  if(!retentionEnabled||!confirm('将按已保存期限永久删除当前符合条件的一批访客咨询和已关闭工单。网页不可撤销，确认执行吗？'))return;
  $('retention-cleanup').disabled=true;
  try{const r=await api('/admin/retention/cleanup',{expected_revision:retentionRevision});await loadMaintenance();$('maintenance-status').textContent=`已删除 ${r.sessions} 个会话、${r.users} 个无关联访客、${r.tokens} 个过期凭据。`;}
  catch(e){$('maintenance-status').textContent=e.message;$('retention-cleanup').disabled=!retentionEnabled;}
};
