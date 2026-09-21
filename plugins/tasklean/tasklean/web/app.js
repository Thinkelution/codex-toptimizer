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
async function list() {
  const data = await api('/api/tasks'); $('tasks').replaceChildren();
  for (const task of data.tasks) { const b = element('button', task.goal, selected === task.id ? 'active' : ''); b.title = task.project; b.onclick = () => choose(task.id); $('tasks').append(b); }
  if (selected && !data.tasks.some(t => t.id === selected)) selected = null;
}
async function choose(id) { if (selected) drafts.set(selected, $('prompt').value); $('prompt').value = drafts.get(id) || ''; selected = id; sessionStorage.setItem('tasklean-selected', id); $('error').hidden = true; await list(); await refresh(); }
async function refresh() {
  if (!selected) return;
  const current = selected;
  const d = await api('/api/detail', {task: current}); if (selected !== current) return;
  $('welcome').hidden = true; $('workspace').hidden = false; $('breadcrumb').textContent = 'Task';
  $('goal').textContent = d.status.goal; $('project').textContent = d.status.project; $('directory').textContent = d.directory;
  busy = d.job.state === 'running'; $('run').disabled = busy; $('run').textContent = busy ? 'Codex is working…' : 'Run with Codex →';
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
$('home').onclick=async e=>{e.preventDefault();if(selected) drafts.set(selected,$('prompt').value);selected=null;sessionStorage.removeItem('tasklean-selected');$('welcome').hidden=false;$('workspace').hidden=true;$('breadcrumb').textContent='Overview';await list();};
for (const id of ['newTask','startTask']) $(id).onclick=()=>$('createDialog').showModal();
$('importTask').onclick=()=>$('importDialog').showModal();
for (const b of document.querySelectorAll('[data-close]')) b.onclick=()=>b.closest('dialog').close();
$('createForm').onsubmit=async e=>{e.preventDefault(); try { const r=await api('/api/tasks',{project:$('projectPath').value,goal:$('taskGoal').value}); $('createDialog').close(); await choose(r.task); } catch(e){$('createDialog').close();error(e);} };
$('importForm').onsubmit=async e=>{e.preventDefault(); try { const r=await api('/api/import',{directory:$('taskPath').value}); $('importDialog').close(); await choose(r.task); } catch(e){$('importDialog').close();error(e);} };
$('demo').onclick=async()=>{ $('demo').disabled=true; $('demo').textContent='Running offline checks…'; try { const r=await api('/api/demo',{}); await choose(r.task); }catch(e){error(e);}finally{$('demo').disabled=false;$('demo').textContent='Try the offline demo';} };
$('promptForm').onsubmit=async e=>{
  e.preventDefault(); if(busy)return;
  const task=selected, prompt=$('prompt').value;
  busy=true; $('run').disabled=true; $('error').hidden=true;
  try {
    await api('/api/run',{task,prompt,model:$('model').value,reasoning:$('reasoning').value,sandbox:$('sandbox').value});
    drafts.delete(task);
    if(selected===task) { $('prompt').value=''; await refresh(); }
  } catch(e) { error(e); if(selected===task) { busy=false; $('run').disabled=false; } }
};
(async()=>{try{await list();const d=await api('/api/doctor');$('connection').textContent=d.codex.available===false?'Demo ready · Codex missing':'Connected locally';if(selected)await refresh();}catch(e){$('connection').textContent='Disconnected';error(e);}})();
setInterval(()=>{if(selected && !document.hidden) refresh().catch(error);},2500);
