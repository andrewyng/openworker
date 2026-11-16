import {Compartment,EditorState,type Extension} from '@codemirror/state';
import {EditorView,keymap,lineNumbers,highlightActiveLineGutter,drawSelection} from '@codemirror/view';
import {HighlightStyle,syntaxHighlighting,foldGutter,foldKeymap,bracketMatching,StreamLanguage} from '@codemirror/language';
import {search,searchKeymap,openSearchPanel} from '@codemirror/search';
import {tags} from '@lezer/highlight';
import {sourceLanguage} from '../lib/source-tree.mjs';
const theme=EditorView.theme({
 '&':{height:'100%',backgroundColor:'#fff',color:'#303843',fontSize:'14px'},
 '&.cm-focused':{outline:'none'},
 '.cm-scroller':{fontFamily:'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',lineHeight:'1.85',overflow:'auto'},
 '.cm-content':{padding:'22px 0 80px',minHeight:'100%'},
 '.cm-line':{padding:'0 24px 0 10px'},
 '.cm-gutters':{backgroundColor:'#fff',color:'#b6bbc4',border:'none',paddingLeft:'12px'},
 '.cm-lineNumbers .cm-gutterElement':{padding:'0 9px',minWidth:'40px'},
 '.cm-activeLineGutter':{backgroundColor:'transparent',color:'#687384'},
 '.cm-foldGutter':{color:'#aab1bc'},
 '.cm-foldPlaceholder':{backgroundColor:'#f2f5fa',border:'none',color:'#7d8aa1'},
 '.cm-selectionBackground, &.cm-focused .cm-selectionBackground':{backgroundColor:'#dce9fc !important'},
 '.cm-matchingBracket':{backgroundColor:'#e6edf7',outline:'1px solid #cbd7e8'},
 '.cm-searchMatch':{backgroundColor:'#fff0bf'},
 '.cm-searchMatch.cm-searchMatch-selected':{backgroundColor:'#fbdc82'},
 '.cm-panels':{backgroundColor:'#f7f9fc',color:'#526076',borderColor:'#e9edf2'},
 '.cm-search':{fontFamily:'-apple-system, system-ui, sans-serif',fontSize:'12px',padding:'12px 16px'},
 '.cm-textfield':{border:'1px solid #dce2ea',borderRadius:'5px',backgroundColor:'#fff',padding:'5px 7px'},
 '.cm-button':{border:'1px solid #dce2ea',borderRadius:'5px',background:'#fff',color:'#526076',fontSize:'11px',textTransform:'none'},
});
const colors=HighlightStyle.define([
 {tag:tags.keyword,color:'#9852a5'},
 {tag:[tags.string,tags.special(tags.string)],color:'#2d7d66'},
 {tag:[tags.number,tags.bool,tags.null],color:'#b36b36'},
 {tag:[tags.function(tags.variableName),tags.function(tags.propertyName)],color:'#356aa6'},
 {tag:[tags.typeName,tags.className],color:'#8b693d'},
 {tag:tags.comment,color:'#919aa8',fontStyle:'italic'},
 {tag:[tags.propertyName,tags.attributeName],color:'#4b6c94'},
 {tag:tags.tagName,color:'#9852a5'},
 {tag:[tags.operator,tags.punctuation],color:'#6f7988'},
 {tag:tags.heading,fontWeight:'600',color:'#34485e'},
 {tag:tags.link,color:'#3973ba',textDecoration:'underline'},
]);
async function language(path:string):Promise<Extension>{
 switch(sourceLanguage(path).id){
  case 'ts':case 'tsx':case 'js':case 'jsx':return (await import('@codemirror/lang-javascript')).javascript({typescript:/\.tsx?$/.test(path),jsx:/\.[jt]sx$/.test(path)});
  case 'rust':return (await import('@codemirror/lang-rust')).rust();
  case 'json':return (await import('@codemirror/lang-json')).json();
  case 'html':return (await import('@codemirror/lang-html')).html();
  case 'css':return (await import('@codemirror/lang-css')).css();
  case 'markdown':return (await import('@codemirror/lang-markdown')).markdown();
  case 'toml':return StreamLanguage.define((await import('@codemirror/legacy-modes/mode/toml')).toml);
  case 'docker':return StreamLanguage.define((await import('@codemirror/legacy-modes/mode/dockerfile')).dockerFile);
  default:return [];
 }
}
export function createSourceEditor(parent:HTMLElement){
 const wrap=new Compartment();let wrapped=true,version=0;
 const view=new EditorView({parent,state:EditorState.create({extensions:[theme,EditorState.readOnly.of(true),EditorView.editable.of(false)]})});
 return {
  async show(file:{path:string;content:string}){
   const current=++version;const syntax=await language(file.path);if(current!==version)return false;
   view.setState(EditorState.create({doc:file.content,extensions:[
    EditorState.readOnly.of(true),EditorView.editable.of(false),
    EditorView.contentAttributes.of({tabindex:'0','aria-label':`Source code: ${file.path}`,'aria-readonly':'true'}),
    lineNumbers(),highlightActiveLineGutter(),drawSelection(),foldGutter(),bracketMatching(),
    syntaxHighlighting(colors),syntax,theme,search({top:true}),keymap.of([...searchKeymap,...foldKeymap]),
    wrap.of(wrapped?EditorView.lineWrapping:[]),
   ]}));
   view.scrollDOM.scrollTop=0;view.scrollDOM.scrollLeft=0;view.requestMeasure();return true;
  },
  search(){openSearchPanel(view);},
  setWrap(value:boolean){wrapped=value;view.dispatch({effects:wrap.reconfigure(value?EditorView.lineWrapping:[])});},
  destroy(){version++;view.destroy();},
 };
}
