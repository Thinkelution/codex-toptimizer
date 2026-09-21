/* SPDX-License-Identifier: GPL-3.0-or-later */
'use strict';
const $ = id => document.getElementById(id);
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.has('token')) { sessionStorage.setItem('tasklean-token', fragment.get('token')); history.replaceState(null, '', '/'); }
const token = sessionStorage.getItem('tasklean-token') || '';
let selected = sessionStorage.getItem('tasklean-selected'), busy = false, logHandle, logOffset, rendered = '';
const drafts = new Map();
function error(e) { $('error').textContent = e.message || String(e); $('error').hidden = false; }
async function api(path, data) {
  const response = await fetch(path, {method: data === undefined ? 'GET' : 'POST', headers: {'X-TaskLean-Token': token, 'Content-Type': 'application/json'}, ...(data === undefined ? {} : {body: JSON.stringify(data)})});
  const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}
function element(tag, text, className) { const el = document.createElement(tag); el.textContent = text; if (className) el.className = className; return el; }
let projects = [], deletedTasks = [], deletingTask = null;
const collapsedProjects = new Set();
function home() {
  selected = null; busy = false; rendered = '';
  sessionStorage.removeItem('tasklean-selected');
  $('welcome').hidden = false; $('workspace').hidden = true; $('breadcrumb').textContent = 'Overview';
}
function renderDeleted() {
  $('deletedTasks').replaceChildren();
  if (!deletedTasks.length) $('deletedTasks').append(element('p', 'No deleted tasks.', 'empty'));
  for (const task of deletedTasks) {
    const row = element('article', '', 'deleted-row'), info = element('div', '');
    info.append(element('strong', task.goal), element('p', task.project, 'mono'));
    const restore = element('button', 'Restore');
    restore.setAttribute('aria-label', 'Restore ' + task.goal);
    restore.onclick = async () => {
      restore.disabled = true;
      try { await api('/api/restore', {task: task.id}); await list(); }
      catch(e) { $('deletedDialog').close(); error(e); }
      finally { restore.disabled = false; }
    };
    row.append(info, restore); $('deletedTasks').append(row);
  }
}
async function list() {
  const data = await api('/api/tasks');
  projects = data.projects; deletedTasks = data.deleted;
  if (selected && !data.tasks.some(t => t.id === selected)) home();
  $('tasks').replaceChildren(); $('knownProjects').replaceChildren();
  for (const project of projects) {
    const option = element('option', project.name); option.value = project.path; $('knownProjects').append(option);
    const group = element('details', '', 'project-group');
    group.open = !collapsedProjects.has(project.path);
    group.ontoggle = () => group.open ? collapsedProjects.delete(project.path) : collapsedProjects.add(project.path);
    const summary = element('summary', '', 'project-summary'); summary.title = project.path;
    summary.append(element('span', project.name, 'project-name'), element('span', String(project.tasks.length), 'project-count'));
    const path = element('small', project.path, 'project-path'); path.title = project.path;
    const add = element('button', '+ New task', 'project-add');
    add.setAttribute('aria-label', 'New task in ' + project.path);
    add.onclick = () => openCreate(project.path);
    group.append(summary, path);
    for (const task of project.tasks) {
      const b = element('button', task.goal, selected === task.id ? 'active' : '');
      b.title = task.goal; b.onclick = () => choose(task.id).catch(error); group.append(b);
    }
    group.append(add); $('tasks').append(group);
  }
  if (!projects.length) $('tasks').append(element('p', 'Create a task to add your first project.', 'empty'));
  $('showDeleted').textContent = 'Recently deleted' + (deletedTasks.length ? ` (${deletedTasks.length})` : '');
  renderDeleted();
}
function openCreate(project) {
  $('projectPath').value = project || (selected ? $('project').textContent : '');
  $('taskGoal').value = ''; $('createDialog').showModal();
}
async function choose(id) { if (selected) drafts.set(selected, $('prompt').value); $('prompt').value = drafts.get(id) || ''; selected = id; sessionStorage.setItem('tasklean-selected', id); $('error').hidden = true; await list(); await refresh(); }
async function refresh() {
  if (!selected) return;
  const current = selected;
  let d;
  try { d = await api('/api/detail', {task: current}); } catch(e) { if(selected === current) throw e; return; }
  if (selected !== current) return;
  $('welcome').hidden = true; $('workspace').hidden = false; $('breadcrumb').textContent = d.status.project.split('/').filter(Boolean).pop() + ' / Task';
  $('goal').textContent = d.status.goal; $('project').textContent = d.status.project; $('directory').textContent = d.directory;
  const wasRunning = busy;
  busy = d.job.state === 'running'; $('deleteTask').disabled = busy;
  if(wasRunning && !busy) loadLimits(true); $('run').disabled = busy; $('run').textContent = busy ? 'Codex is working…' : 'Run with Codex →';
  $('runState').textContent = busy ? 'Running · ' + Math.floor((Date.now()/1000-d.job.started)) + 's' : (d.job.state || 'Ready');
  const usage = (d.report.model_turns || []);
  const rows = Array.isArray(usage) ? usage : [];
  const measured = rows.map(r => r.usage).filter(u => u && u.available);
  for (const [id,key] of [['inputTokens','input_tokens'],['cachedTokens','cached_input_tokens'],['outputTokens','output_tokens']]) $(id).textContent = measured.length ? measured.reduce((s,u)=>s+(u[key]||0),0).toLocaleString() : '—';
  $('turnCount').textContent = rows.length || d.turns.length;
  $('usageNote').textContent = `${measured.length} ${measured.length === 1 ? "turn" : "turns"} with reported usage. Cached input is part of input. Savings and subscription quota are not measured.`;
  const signature = JSON.stringify([current, d.turns, d.status.notes, d.commands, d.job.state, d.job.error]);
  if (signature === rendered) return;
  rendered = signature;
  const conversation = $('conversation'); conversation.replaceChildren();
  if (!d.turns.length) conversation.append(element('p', 'No Codex turns yet. Give this task its next step below.', 'empty'));
  for (const t of d.turns) { const card=element('article','','turn'); if(t.prompt) card.append(element('small','YOU'), element('pre',t.prompt)); card.append(element('small',`CODEX · ${t.execution_status} · ${t.sandbox} · ${t.elapsed_seconds}s`),element('pre',t.answer || 'No final answer returned. Inspect the saved turn files for details.')); conversation.append(card); }
  if (busy) conversation.append(element('p','Codex is working on your prompt. Results and usage appear when the turn finishes.','running'));
  if (d.job.error) conversation.append(element('p',d.job.error,'running'));
  $('notes').replaceChildren();
  for (const n of d.status.notes || []) { const card=element('article','','note'); card.append(element('small',`${n.kind} · ${n.key}${n.stale ? ' · STALE EVIDENCE' : ''}`),element('p',n.text)); $('notes').append(card); }
  if (!$('notes').children.length) $('notes').append(element('p','Decisions and observations will appear here when saved by the task.','empty'));
  $('logs').replaceChildren();
  for (const c of d.commands) { const b=element('button',`${c.execution_status} · ${c.command.join(' ')}`,'log-button'); b.onclick=()=>showLog(c.artifact); $('logs').append(b); }
  if (!$('logs').children.length) $('logs').append(element('p','Commands captured with TaskLean will appear here.','empty'));
}
async function showLog(id) { logHandle=id; logOffset=0; $('logText').textContent=''; $('logDialog').showModal(); await moreLog(); }
async function moreLog() { try { const r=await api('/api/artifact',{task:selected,id:logHandle,offset:logOffset}); $('logText').textContent+=r.text; logOffset=r.next_offset; $('moreLog').hidden=!r.more; } catch(e) { $('logDialog').close(); error(e); } }
$('moreLog').onclick=moreLog;
$('home').onclick=async e=>{e.preventDefault();if(selected) drafts.set(selected,$('prompt').value);home();await list();};
for (const id of ['newTask','startTask']) $(id).onclick=()=>openCreate();
$('showDeleted').onclick=async()=>{try{await list();$('deletedDialog').showModal();}catch(e){error(e);}};
$('deleteTask').onclick=()=>{if(busy || !selected)return;deletingTask=selected;$('deleteGoal').textContent=$('goal').textContent;$('deleteDialog').showModal();};
$('confirmDelete').onclick=async()=>{
  const task=deletingTask; $('confirmDelete').disabled=true;
  try { await api('/api/delete',{task}); drafts.delete(task); if(selected===task)home(); $('deleteDialog').close(); await list(); }
  catch(e){$('deleteDialog').close();error(e);}
  finally{$('confirmDelete').disabled=false;deletingTask=null;}
};
$('importTask').onclick=()=>$('importDialog').showModal();
for (const b of document.querySelectorAll('[data-close]')) b.onclick=()=>b.closest('dialog').close();
$('createForm').onsubmit=async e=>{e.preventDefault(); try { const r=await api('/api/tasks',{project:$('projectPath').value,goal:$('taskGoal').value}); $('createDialog').close(); await choose(r.task); } catch(e){$('createDialog').close();error(e);} };
$('importForm').onsubmit=async e=>{e.preventDefault(); try { const r=await api('/api/import',{directory:$('taskPath').value}); $('importDialog').close(); await choose(r.task); } catch(e){$('importDialog').close();error(e);} };
$('demo').onclick=async()=>{ $('demo').disabled=true; $('demo').textContent='Running offline checks…'; try { const r=await api('/api/demo',{}); await choose(r.task); }catch(e){error(e);}finally{$('demo').disabled=false;$('demo').textContent='Try the offline demo';} };
$('promptForm').onsubmit=async e=>{
  e.preventDefault(); if(busy)return;
  const task=selected, prompt=$('prompt').value;
  busy=true; $('run').disabled=true; $('deleteTask').disabled=true; $('error').hidden=true;
  try {
    await api('/api/run',{task,prompt,model:$('model').value,reasoning:$('reasoning').value,sandbox:$('sandbox').value});
    drafts.delete(task);
    if(selected===task) { $('prompt').value=''; await refresh(); }
  } catch(e) { error(e); if(selected===task) { busy=false; $('run').disabled=false; $('deleteTask').disabled=false; } }
};
(async()=>{try{await list();const d=await api('/api/doctor');$('connection').textContent=d.codex.available===false?'Demo ready · Codex missing':'Connected locally';if(selected)await refresh();}catch(e){$('connection').textContent='Disconnected';error(e);}})();
setInterval(()=>{if(selected && !document.hidden) refresh().catch(error);},2500);

let limitData=null, limitsLoading=false, limitsClockOffset=0;
function windowLabel(window) {
  const minutes=window.window_minutes;
  if(minutes===10080)return 'Weekly';
  if(minutes===300)return '5-hour';
  if(minutes===1440)return 'Daily';
  if(minutes && minutes%1440===0)return `${minutes/1440}-day`;
  if(minutes && minutes%60===0)return `${minutes/60}-hour`;
  return minutes ? `${minutes}-minute` : `${window.slot === 'primary' ? 'Primary' : 'Secondary'} window`;
}
function resetText(timestamp, now) {
  if(timestamp===null)return 'Reset time unavailable';
  const minutes=Math.ceil((timestamp*1000-now)/60000);
  if(minutes<=0)return 'Reset time passed · awaiting fresh data';
  const days=Math.floor(minutes/1440), hours=Math.floor(minutes%1440/60), mins=minutes%60;
  return 'Resets in '+[days?`${days}d`:null,hours?`${hours}h`:null,mins?`${mins}m`:null].filter(Boolean).join(' ');
}
function renderLimits() {
  if(!limitData)return;
  const now=Date.now()+limitsClockOffset;
  const host=$('limitWindows'); host.replaceChildren();
  for(const bucket of limitData.buckets) for(const window of bucket.windows) {
    const card=element('article','','limit-card'+(limitData.stale?' stale':''));
    const name=bucket.id==='codex' ? 'Codex' : bucket.name;
    card.append(element('span',`${name} · ${windowLabel(window)}`,'limit-label'));
    const known=window.remaining_percent!==null;
    card.append(element('strong',known?`${window.remaining_percent}% remaining`:'Unavailable'));
    if(known) {
      const progress=element('progress','');progress.max=100;progress.value=window.remaining_percent;
      progress.setAttribute('aria-label',`${name} ${windowLabel(window)} limit remaining`);
      if(window.remaining_percent<=10)progress.className='low';
      card.append(progress);
    }
    const reset=element('p',resetText(window.resets_at,now),'limit-reset');
    if(window.resets_at!==null) reset.title=new Date(window.resets_at*1000).toLocaleString();
    card.append(reset);
    if(window.resets_at!==null)card.append(element('small',new Date(window.resets_at*1000).toLocaleString()));
    host.append(card);
  }
  const age=limitData.checked_at===null ? null : Math.max(0,Math.floor((now-limitData.checked_at*1000)/60000));
  const updated=age===null ? '' : `Last checked ${age===0?'just now':age+'m ago'}. `;
  $('limitsStatus').textContent=limitData.error ? `${limitData.available?'Stale — showing last known values. ':''}${updated}${limitData.error}` : `${updated}Refreshes every minute while this tab is visible. Only windows reported by Codex are shown.`;
}
async function loadLimits(force=false) {
  if(limitsLoading)return;
  limitsLoading=true; $('refreshLimits').disabled=true;
  try {
    limitData=await api('/api/limits'+(force?'?refresh=1':''));
    limitsClockOffset=limitData.server_time*1000-Date.now();
    renderLimits();
  } catch(e) {
    if(limitData) {limitData={...limitData,stale:true,error:'Could not refresh account limits. Reconnect to the local dashboard and retry.'};renderLimits();}
    else $('limitsStatus').textContent='Account limits unavailable. Connect using the launch URL printed by tasklean ui.';
  } finally {limitsLoading=false;$('refreshLimits').disabled=false;}
}
$('refreshLimits').onclick=()=>loadLimits(true);
loadLimits();
setInterval(()=>{if(!document.hidden)loadLimits();},60000);
setInterval(()=>{if(!document.hidden)renderLimits();},15000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)loadLimits();});

let feedbackAttempt = null;
$('feedbackButton').onclick = () => $('feedbackDialog').showModal();
$('feedbackForm').onsubmit = async event => {
  event.preventDefault();
  if ($('sendFeedback').disabled) return;
  const fields = {message: $('feedbackMessage').value.trim(), rating: $('feedbackRating').value ? Number($('feedbackRating').value) : null, email: $('feedbackEmail').value.trim(), consent: true};
  const signature = JSON.stringify(fields);
  if (!feedbackAttempt || feedbackAttempt.signature !== signature) feedbackAttempt = {signature, id: crypto.randomUUID()};
  $('sendFeedback').disabled = true; $('feedbackStatus').textContent = 'Sending feedback…';
  try {
    const result = await api('/api/feedback', {...fields, submission_id: feedbackAttempt.id});
    $('feedbackForm').reset(); feedbackAttempt = null;
    $('feedbackStatus').textContent = 'Thank you — Thinkelution received your feedback. Reference: ' + result.submission_id;
  } catch(e) { $('feedbackStatus').textContent = e.message; }
  finally { $('sendFeedback').disabled = false; }
};
