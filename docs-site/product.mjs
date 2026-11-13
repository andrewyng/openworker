import sdkPages from './pages.json' with {type:'json'};
import desktopPages from './desktop-pages.json' with {type:'json'};
import {navigation as sdkNavigation} from './src/lib/navigation.mjs';
import {desktopNavigation} from './src/lib/desktop-navigation.mjs';

// An explicit manifest per product keeps private notes and unreviewed pages out
// of both the static output and search. SDK remains the default build target.
export function docsProduct(env=process.env) {
 const id=env.OW_DOCS_PRODUCT??'sdk';
 if(!['sdk','desktop'].includes(id))throw Error(`Unknown docs product: ${id}`);
 const desktop=id==='desktop';
 return {
  id,
  label:desktop?'Desktop App':'Harness SDK',
  title:desktop?'OpenWorker Desktop':'OpenWorker SDK',
  base:env.OW_DOCS_BASE??`/docs/${id}`,
  content:desktop?'desktop':'docs',
  pages:desktop?desktopPages:sdkPages,
  navigation:desktop?desktopNavigation:sdkNavigation,
 };
}
