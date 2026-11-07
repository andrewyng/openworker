import {defineConfig} from 'astro/config';
import sitemap from '@astrojs/sitemap';
export default defineConfig({
 site:'https://openworker.com',
 base:process.env.OW_DOCS_BASE??'/docs/sdk',
 devToolbar:{enabled:false},
 integrations:[sitemap({filter:url=>!url.includes('/previews/')})],
});
