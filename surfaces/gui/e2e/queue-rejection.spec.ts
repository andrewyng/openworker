import { expect } from '@playwright/test';
import { test } from './fixtures';
test('rejected queued input must not leave the session running',async({page})=>{
 let complete:()=>void=()=>{};
 await page.routeWebSocket(/\/ws\/session\//,ws=>{
  const sid=ws.url().split('/ws/session/')[1].split('?')[0];
  ws.send(JSON.stringify({type:'ready',data:{running:sid==='resume-live-1'}}));
  if(sid==='resume-live-1')complete=()=>ws.send(JSON.stringify({type:'turn_done',data:{}}));
  ws.onMessage(raw=>{const m=JSON.parse(String(raw));if(m.type==='user_message')ws.send(JSON.stringify({type:'input_rejected',data:{error:'Message too long',request_id:m.request_id}}));});
 });
 await page.goto('/');await page.getByRole('button',{name:/Show more/}).first().click();await page.getByTitle('Long audit').click();
 await expect(page.getByRole('button',{name:/Stop/})).toBeVisible();const box=page.getByPlaceholder(/Ask the coworker/);await box.fill('rejected followup');await box.press('Enter');
 complete();await expect(page.getByText('Message too long').first()).toBeVisible();
 await expect(page.getByRole('button',{name:/Stop/})).toHaveCount(0);
 await expect(page.getByTestId('composer-queue')).toContainText('rejected followup');
});
