import {defineConfig} from 'astro/config';
import sitemap from '@astrojs/sitemap';
import {docsProduct} from './product.mjs';
const product=docsProduct();
export default defineConfig({
 site:'https://openworker.com',
 base:product.base,
 outDir:product.id==='desktop'?'./dist-desktop':'./dist',
 cacheDir:product.id==='desktop'?'./node_modules/.astro-desktop':'./node_modules/.astro',
 devToolbar:{enabled:false},
 integrations:[sitemap({filter:url=>!url.includes('/previews/')})],
});
