import { expect } from '@playwright/test';
import { test } from './fixtures';
test('queued messages stay in their original session', async ({page}) => {
 const sent: {sid:string,text:string}[]=[];
 await page.routeWebSocket(/\/ws\/session\//,ws=>{
  const sid=ws.url().split('/ws/session/')[1].split('?')[0];
  ws.send(JSON.stringify({type:'ready',data:{running:sid==='resume-live-1'}}));
  ws.onMessage(raw=>{const m=JSON.parse(String(raw));if(m.type==='user_message')sent.push({sid,text:m.text});});
 });
 await page.goto('/');await page.getByRole('button',{name:/Show more/}).first().click();
 await page.getByTitle('Long audit').click();await expect(page.getByRole('button',{name:/Stop/})).toBeVisible();
 const box=page.getByPlaceholder(/Ask the coworker/);await box.fill('private audit followup');await box.press('Enter');
 await page.getByTitle('Draft the launch note').first().click();await expect(page.getByRole('button',{name:'Send',exact:true})).toBeVisible();
 await expect.poll(()=>sent).toEqual([]);
});

test('queue sends one message per acknowledged turn', async ({page}) => {
 const sent:string[]=[];let complete:()=>void=()=>{};
 await page.routeWebSocket(/\/ws\/session\//,ws=>{
  const sid=ws.url().split('/ws/session/')[1].split('?')[0];
  const send=(type:string,data={})=>ws.send(JSON.stringify({type,data}));
  send('ready',{running:sid==='resume-live-1'});
  if(sid==='resume-live-1')complete=()=>send('turn_done');
  ws.onMessage(raw=>{const m=JSON.parse(String(raw));if(m.type==='user_message'){sent.push(m.text);send('turn_start',{input:m.text,request_id:m.request_id});}});
 });
 await page.goto('/');await page.getByRole('button',{name:/Show more/}).first().click();await page.getByTitle('Long audit').click();
 await expect(page.getByRole('button',{name:/Stop/})).toBeVisible();const box=page.getByPlaceholder(/Ask the coworker/);
 for(const text of ['first','second']){await box.fill(text);await box.press('Enter');}
 complete();await expect.poll(()=>sent).toEqual(['first']);
 await expect(page.getByTestId('composer-queue')).toContainText('second');
 complete();await expect.poll(()=>sent).toEqual(['first','second']);
 await expect(page.getByTestId('composer-queue')).toHaveCount(0);
});


test('an attached queued message is acknowledged by ID', async ({ page }) => {
  let complete: () => void = () => {};
  await page.routeWebSocket(/\/ws\/session\//, (ws) => {
    const sid = ws.url().split('/ws/session/')[1].split('?')[0];
    ws.send(JSON.stringify({ type: 'ready', data: { running: sid === 'resume-live-1' } }));
    if (sid === 'resume-live-1') complete = () => ws.send(JSON.stringify({ type: 'turn_done', data: {} }));
    ws.onMessage((raw) => {
      const m = JSON.parse(String(raw));
      if (m.type === 'user_message') {
        expect(m.attachments).toHaveLength(1);
        ws.send(JSON.stringify({ type: 'turn_start', data: {
          input: [{ type: 'text', text: m.text }, { type: 'text', text: 'attachment framing' }],
          request_id: m.request_id,
        } }));
      }
    });
  });
  await page.goto('/');
  await page.getByRole('button', { name: /Show more/ }).first().click();
  await page.getByTitle('Long audit').click();
  await expect(page.getByRole('button', { name: /Stop/ })).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles({
    name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('attached notes'),
  });
  await expect(page.getByText('notes.txt').first()).toBeVisible();
  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill('inspect attached notes');
  await box.press('Enter');
  await expect(page.getByTestId('composer-queue')).toBeVisible();
  complete();
  await expect(page.getByTestId('composer-queue')).toHaveCount(0);
  await expect(page.getByText('inspect attached notes', { exact: false })).toBeVisible();
});
