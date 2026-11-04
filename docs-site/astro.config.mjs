import {defineConfig} from 'astro/config';
import starlight from '@astrojs/starlight';
export default defineConfig({
 site:'https://openworker.com',base:process.env.OW_DOCS_BASE??'/docs/sdk',devToolbar:{enabled:false},
 integrations:[starlight({expressiveCode:{themes:['github-dark']},title:'OpenWorker SDK',customCss:['./src/custom.css'],
 components:{Footer:'./src/components/Footer.astro',ThemeSelect:'./src/components/ThemeToggle.astro'},
 social:[{icon:'github',label:'GitHub',href:'https://github.com/andrewyng/openworker/tree/openworker-harness-sdk/harness'}],
 sidebar:[
 {label:'Get started',items:[{label:'Overview',slug:''},{label:'Desktop app',slug:'desktop'},{label:'Web app',slug:'server'}]},
 {label:'Build',items:[{label:'Models and keys',slug:'providers'},{label:'Tools and MCP',slug:'tools'},{label:'Sessions and teams',slug:'sessions'}]},
 {label:'Deploy',items:[{label:'Deployment options',slug:'deployment'},{label:'Private Kubernetes cluster',slug:'private-kubernetes'},{label:'AWS',slug:'aws'},{label:'GCP',slug:'gcp'},{label:'Azure',slug:'azure'}]},
 {label:'Safety & governance',items:[{label:'NVIDIA OpenShell',slug:'openshell'},{label:'Guardrails and hooks',slug:'guardrails'},{label:'Agent alignment',slug:'alignment'},{label:'Compliance and SIEM',slug:'compliance'}]},
 {label:'Operate',items:[{label:'Observability',slug:'observability'},{label:'Compatibility and upgrades',slug:'releases'},{label:'Troubleshooting',slug:'troubleshooting'}]}
 ]
 })]
});
