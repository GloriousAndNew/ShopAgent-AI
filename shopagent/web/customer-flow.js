// 顾客入口、人工咨询与商家处理；不通过模型发送人工回复。
const renderedHumanIDs=new Set();
let productionMode=false;
let replyRefreshing=false, unreadRefreshing=false, merchantFilter='pending';
function guestUser(){return user?.role==='buyer'&&user.user_id.startsWith('guest_');}
function applyEntryPresentation(runtime){
  productionMode=runtime.environment==='production';
  if(productionMode){$('demo-account-label').classList.add('hidden');$('formal-account-label').classList.remove('hidden');$('password').value='';document.querySelector('#login-form .small').textContent='使用本机初始化的正式管理员账号；不提供默认密码。';}

  if(entryPath==='/customer'){
    $('login-form').classList.add('hidden');$('customer-start').classList.remove('hidden');$('entry-links').classList.add('hidden');
    document.querySelector('.login-card h1').textContent='欢迎咨询店铺客服';document.querySelector('.login-card>p').textContent='查商品、看库存、了解优惠与政策。订单和售后处理可联系人工客服。';
    document.querySelector('.login-card>.notice').textContent='当前为本地试运行，资料仍含示例。未接真实订单，不执行支付或退款。';
    for(const [i,node] of [...document.querySelectorAll('.art-chip')].entries())node.textContent=['商品查询','库存优惠','人工咨询'][i]??'';
    $('welcome').querySelector('p').textContent='可以咨询商品、库存、优惠和店铺政策。订单与售后处理请联系人工客服。';
    $('welcome-prompts').replaceChildren();$('quick-prompts').replaceChildren();
    for(const [title,text] of [['选购商品','200 元以内的降噪耳机'],['核实库存','P001 黑色还有货吗'],['了解政策','发货和运费政策'],['优惠咨询','有哪些优惠活动']]){const b=el('button',undefined,'prompt');b.append(el('strong',title),el('small',text));b.onclick=()=>send(text);$('welcome-prompts').append(b);}
    for(const text of ['200 元以内的降噪耳机','发货和运费政策']){const b=el('button',text);b.onclick=()=>send(text);$('quick-prompts').append(b);}
    $('mode-badge').textContent='商品咨询 · 本地试运行';$('mode-composer').textContent='当前资料含示例，AI回答请核对。订单和售后请联系人工，不执行交易。';
    $('mode-title').textContent='店铺咨询';$('mode-detail').textContent='商品、库存、优惠与政策';
    document.querySelector('.brand').href='/customer';
  }else if(entryPath==='/merchant'){
    $('username').value='admin';document.querySelector('.login-card h1').textContent='商家工作台';document.querySelector('.login-card>p').textContent='维护店铺资料，查看顾客咨询并回复。当前仍使用本地示例账号。';
  }
  if(productionMode){
    document.querySelector('.login-card>.notice').textContent='商品咨询模式；未接真实订单，不执行支付或退款。';
    if(entryPath==='/merchant')document.querySelector('.login-card>p').textContent='使用正式管理员账号维护资料并回复顾客。';
    $('mode-title').textContent=runtime.mode==='llm'?'多 Agent 协作':'规则测试模式';$('mode-detail').textContent='正式资料 · 商品咨询';
    $('mode-badge').textContent=runtime.mode==='llm'?'商品咨询 · 正式资料':'商品咨询 · 规则测试';$('mode-composer').textContent='答复请核对商品与政策引用；库存是商家维护记录。订单和售后处理请联系人工。';
    for(const [i,node] of [...document.querySelectorAll('.art-chip')].entries())node.textContent=['商品查询','库存优惠','人工咨询'][i]??'';
    $('starter-import').classList.add('hidden');$('policy-starter-select').classList.add('hidden');$('load-policy-starter').classList.add('hidden');
  }
  applyStorefront(runtime.storefront);loadPrivacy();
}
function configureUserEntry(){
  $('agents-nav').classList.toggle('hidden',guestUser());$('customer-tickets-nav').classList.toggle('hidden',user.role==='admin');
  if(guestUser()){$('user-label').textContent='访客';$('user-role').textContent='商品咨询访客';$('avatar').textContent='客';}
}
$('start-guest').onclick=async()=>{
  const b=$('start-guest');b.disabled=true;$('guest-error').textContent='';
  try{if(!privacyVersion||!$('privacy-ack').checked)throw new Error('请先阅读并勾选咨询数据说明。');const value=await api('/auth/guest',{acknowledged:true,notice_version:privacyVersion});token=value.token;sessionStorage.setItem(storagePrefix+'-token',token);resetChat();await enter();}
  catch(e){$('guest-error').textContent=e.message;}finally{b.disabled=false;}
};
async function updateUnread(){
  if(!user||user.role==='admin'||unreadRefreshing)return;
  unreadRefreshing=true;try{const values=await api('/customer/tickets');const count=values.reduce((n,t)=>n+t.unread,0);$('unread-badge').textContent=count?`(${count}条未读)`:' ';}
  catch(e){$('reply-check-status').textContent=e.message;}finally{unreadRefreshing=false;}
}
async function markLoadedRead(sid,values){const last=values.at(-1)?.id;if(last)await api(`/sessions/${encodeURIComponent(sid)}/read`,{message_id:last});}
async function refreshHumanReplies(){
  if(!sessionId){$('reply-check-status').textContent='暂无当前会话，请在“我的人工咨询”选择咨询。';return;}
  if(busy||replyRefreshing)return;replyRefreshing=true;$('refresh-replies').disabled=true;
  try{const sid=sessionId,values=await api(`/sessions/${encodeURIComponent(sid)}/messages`);if(sid!==sessionId)return;
    let count=0;for(const m of values){if(m.result?.source==='human'&&!renderedHumanIDs.has(m.id)){renderResult(m.result,addMessage('assistant',m.content));renderedHumanIDs.add(m.id);count++;}}
    await markLoadedRead(sid,values);await updateUnread();$('reply-check-status').textContent=count?`收到${count}条人工回复`:'已检查，暂无新的人工回复';scrollBottom();
  }catch(e){$('reply-check-status').textContent=e.message;}finally{replyRefreshing=false;$('refresh-replies').disabled=false;}
}
$('refresh-replies').onclick=refreshHumanReplies;
$('open-handoff').onclick=()=>{if(busy)return;$('handoff-panel').classList.remove('hidden');$('handoff-message').focus();};
$('cancel-handoff').onclick=()=>{$('handoff-panel').classList.add('hidden');};
$('handoff-form').onsubmit=async(e)=>{e.preventDefault();if(busy)return;const message=$('handoff-message').value.trim();if(message.length<2){$('handoff-error').textContent='请描述需要处理的问题。';return;}
  busy=true;$('submit-handoff').disabled=$('send').disabled=true;$('handoff-error').textContent='';
  try{const value=await api('/customer/handoff',{message,session_id:sessionId});sessionId=value.session_id;sessionStorage.setItem(storagePrefix+'-session',sessionId);addMessage('user',message);const b=addMessage('assistant',value.reply);renderResult(value,b);$('handoff-message').value='';$('handoff-panel').classList.add('hidden');await loadSessions();await updateUnread();scrollBottom();}
  catch(error){$('handoff-error').textContent=error.message;}finally{busy=false;$('submit-handoff').disabled=$('send').disabled=false;}
};
async function loadCustomerTickets(){
  try{const values=await api('/customer/tickets');$('customer-ticket-list').replaceChildren();
    if(!values.length)$('customer-ticket-list').append(el('p','暂无人工咨询。回到顾客咨询，点击“联系人工客服”描述问题。','muted'));
    for(const t of values){const c=el('article',undefined,'ticket-row');c.append(el('span',`${t.status}${t.unread?` · ${t.unread}条未读回复`:''}`,'tag'),el('h3',t.content),el('p',`${t.id} · ${time(t.created_at)}`,'muted'));const b=el('button','打开会话查看回复','secondary');b.onclick=()=>restore(t.session_id);c.append(b);$('customer-ticket-list').append(c);}
    $('customer-ticket-status').textContent='状态已刷新；商家不保证即时回复。';await updateUnread();
  }catch(e){$('customer-ticket-status').textContent=e.message;}
}
$('customer-tickets-nav').onclick=()=>{if(!busy){selectView('customer-tickets','我的人工咨询');loadCustomerTickets();}};
$('refresh-customer-tickets').onclick=loadCustomerTickets;

async function loadMerchantTickets(){
  const root=$('tickets');root.replaceChildren();const toolbar=el('div',undefined,'catalog-toolbar'),filter=el('select');filter.setAttribute('aria-label','筛选人工咨询');
  for(const [v,text] of [['pending','待处理'],['replied','已回复'],['closed','已关闭'],['all','全部']]){const o=el('option',text);o.value=v;filter.append(o);}filter.value=merchantFilter;filter.onchange=()=>{merchantFilter=filter.value;loadMerchantTickets();};toolbar.append(filter);root.append(toolbar);
  const values=await api(`/admin/tickets?status=${merchantFilter}`);if(!values.length)root.append(el('p','此状态下暂无咨询。','muted'));
  for(const t of values){const row=el('article',undefined,'ticket-row');row.append(el('p',`${t.id} · ${t.user_id.startsWith('guest_')?'访客 '+t.user_id.slice(-6):t.user_id}`),el('h3',t.content),el('p',time(t.created_at),'muted'));
    const detail=el('details'),summary=el('summary','查看会话与回复记录'),history=el('div',undefined,'ticket-history');detail.append(summary,history);let loaded=false;
    detail.ontoggle=async()=>{if(!detail.open||loaded)return;try{const value=await api(`/admin/tickets/${encodeURIComponent(t.id)}`);history.replaceChildren();for(const m of value.messages){history.append(el('p',`${m.role==='user'?'顾客':m.result?.source==='human'?'人工客服':'AI / 系统'} · ${time(m.created_at)}`,'muted'),el('p',m.content));}loaded=true;}catch(e){history.textContent=e.message;}};row.append(detail);
    const input=el('textarea');input.placeholder='输入给顾客的回复';input.maxLength=3000;input.setAttribute('aria-label',`回复咨询 ${t.id}`);
    const button=el('button','发送人工回复','secondary'),state=el('p',undefined,'muted'),status=el('span',t.status,'tag');let pending=null,current=t.status;
    const close=el('button',current==='已关闭'?'重新打开':'关闭咨询','secondary');button.disabled=input.disabled=current==='已关闭';
    button.onclick=async()=>{const message=input.value.trim();if(!message){state.textContent='请先输入回复。';return;}if(!pending||pending.message!==message)pending={message,request_key:crypto.randomUUID()};button.disabled=close.disabled=true;
      try{await api(`/admin/tickets/${encodeURIComponent(t.id)}/reply`,pending);input.value='';pending=null;current='已回复';status.textContent=current;state.textContent='回复已保存到顾客原会话；顾客可在“我的人工咨询”查看。';loaded=false;}
      catch(e){state.textContent=e.message;}finally{button.disabled=current==='已关闭';close.disabled=false;}
    };
    close.onclick=async()=>{close.disabled=button.disabled=true;try{const target=current==='已关闭'?'待处理':'已关闭';await api(`/admin/tickets/${encodeURIComponent(t.id)}/status`,{status:target,expected_status:current});current=target;status.textContent=current;close.textContent=current==='已关闭'?'重新打开':'关闭咨询';state.textContent=current==='已关闭'?'咨询已关闭；历史回复仍保留。':'咨询已重新打开，可以回复。';}
      catch(e){state.textContent=e.message;}finally{close.disabled=false;button.disabled=input.disabled=current==='已关闭';}
    };
    row.append(input,button,close,status,state);root.append(row);
  }
  const ready=await api('/admin/launch-readiness');const readiness=el('details');readiness.append(el('summary','公网部署准备 · 尚未满足上线条件'));for(const text of ready.blockers)readiness.append(el('p','待完成：'+text,'muted'));root.append(readiness);
}
// 只更新未读提示，避免刷新替换当前草稿或丢失正在输入的问题。
setInterval(()=>{if(document.visibilityState==='visible'&&user&&!busy)updateUnread();},30000);
if(token)enter().catch(()=>exit(false));
else refreshRuntime().catch(e=>{$('guest-error').textContent=e.message;});
