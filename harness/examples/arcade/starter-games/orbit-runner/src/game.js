import * as THREE from '../vendor/three.module.js';
const canvas=document.querySelector('#game'),intro=document.querySelector('#intro'),scoreText=document.querySelector('#score');
let renderer;
try{renderer=new THREE.WebGLRenderer({canvas,antialias:true});}catch{document.querySelector('h1').textContent='3D needs WebGL';document.querySelector('#intro p').textContent='Enable graphics acceleration, or choose a Canvas game.';document.querySelector('#start').disabled=true;throw new Error('WebGL unavailable');}
renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.setClearColor(0x10182b);renderer.outputColorSpace=THREE.SRGBColorSpace;
const scene=new THREE.Scene();scene.fog=new THREE.FogExp2(0x10182b,.018);const camera=new THREE.PerspectiveCamera(55,1,.1,120);camera.position.set(0,6,12);camera.lookAt(0,0,-12);
scene.add(new THREE.HemisphereLight(0xbecaff,0x15202a,2.5));const light=new THREE.DirectionalLight(0xffffff,3);light.position.set(4,9,5);scene.add(light);
const track=new THREE.Mesh(new THREE.BoxGeometry(8,.3,140),new THREE.MeshStandardMaterial({color:0x293654,roughness:.8}));track.position.set(0,-.3,-50);scene.add(track);
for(const x of [-4,-1.33,1.33,4]){const line=new THREE.Mesh(new THREE.BoxGeometry(.035,.025,140),new THREE.MeshBasicMaterial({color:x===-4||x===4?0xc0f398:0x50617f}));line.position.set(x,-.13,-50);scene.add(line);}
const player=new THREE.Mesh(new THREE.IcosahedronGeometry(.5,1),new THREE.MeshStandardMaterial({color:0xd1f498,metalness:.15,roughness:.3}));player.position.set(0,.55,2);scene.add(player);
const stars=new THREE.BufferGeometry(),positions=[];for(let i=0;i<160;i++)positions.push((Math.random()-.5)*100,4+Math.random()*35,-Math.random()*100);stars.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));scene.add(new THREE.Points(stars,new THREE.PointsMaterial({color:0xbac8ff,size:.08})));
const coinGeo=new THREE.TorusGeometry(.42,.12,8,18),blockGeo=new THREE.BoxGeometry(1.1,1.1,1.1),coinMat=new THREE.MeshStandardMaterial({color:0xffd887,emissive:0x442900,metalness:.5,roughness:.3}),blockMat=new THREE.MeshStandardMaterial({color:0xf47e7e,roughness:.5});
let objects=[],lane=1,score=0,running=false,last=performance.now(),spawn=0,elapsed=0;
function resize(){renderer.setSize(innerWidth,innerHeight,false);camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();}addEventListener('resize',resize);resize();
function move(d){lane=Math.max(0,Math.min(2,lane+d));}document.querySelector('#left').onclick=()=>move(-1);document.querySelector('#right').onclick=()=>move(1);addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','a','d','A','D'].includes(e.key)){e.preventDefault();move(['ArrowLeft','a','A'].includes(e.key)?-1:1);}});
function start(){for(const o of objects)scene.remove(o);objects=[];lane=1;score=0;elapsed=0;spawn=.5;running=true;scoreText.textContent='0 points';intro.hidden=true;canvas.focus();}
document.querySelector('#start').onclick=start;document.querySelector('#restart').onclick=start;
function finish(){running=false;intro.hidden=false;document.querySelector('h1').innerHTML='One more<br><em>orbit?</em>';document.querySelector('#intro p').textContent=score+' points. A new best is one run away.';document.querySelector('#start').innerHTML='Play again <span>↗</span>';}
function frame(now){const dt=Math.min((now-last)/1000,.05);last=now;player.rotation.y+=dt;player.rotation.z+=dt*.3;player.position.x+=((lane-1)*2.5-player.position.x)*Math.min(1,dt*14);player.position.y=.65+Math.sin(now*.004)*.08;
if(running){elapsed+=dt;spawn-=dt;if(spawn<=0){spawn=Math.max(.36,.85-elapsed*.004);const coin=Math.random()>.3,o=new THREE.Mesh(coin?coinGeo:blockGeo,coin?coinMat:blockMat);o.userData.coin=coin;o.userData.lane=Math.floor(Math.random()*3);o.position.set((o.userData.lane-1)*2.5,.65,-55);scene.add(o);objects.push(o);}
for(let i=objects.length-1;i>=0;i--){const o=objects[i];o.position.z+=dt*(12+Math.min(elapsed*.15,10));if(o.userData.coin)o.rotation.y+=dt*2;if(Math.abs(o.position.z-2)<.7&&Math.abs(o.position.x-player.position.x)<.8){if(o.userData.coin){score+=10;scoreText.textContent=score+' points';}else finish();scene.remove(o);objects.splice(i,1);}else if(o.position.z>14){scene.remove(o);objects.splice(i,1);}}}
renderer.render(scene,camera);requestAnimationFrame(frame);}requestAnimationFrame(frame);

