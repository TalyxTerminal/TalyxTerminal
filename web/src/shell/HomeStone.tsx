import { useEffect, useRef } from "react";
import { tokenValue } from "@talyx/sdk";

/* Home's background: black glass with dark amber running slowly through it. Half resolution at about 30 fps, moving
   whenever Home is on screen. It stops when the page is hidden (the desktop hides it when the window is minimised or in
   the tray) and under an open workspace or another screen, so Home holds no GPU while the work happens elsewhere. It
   does not stop on lost focus: WebView2 reports focus only once the page is clicked, so a window just opened or seen
   beside another would stand still. With reduced motion it draws one frame; without WebGL the fallback gradient shows. */

const vertex = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
const fragment = `precision highp float;uniform vec2 R;uniform float T;
uniform vec3 STONE,BODY,GLOW,VEIN,SHEEN,WARM;
float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(h(i),h(i+vec2(1.,0.)),f.x),mix(h(i+vec2(0.,1.)),h(i+vec2(1.,1.)),f.x),f.y);}
float fbm(vec2 p){float v=0.,a=.5;mat2 m=mat2(1.6,1.2,-1.2,1.6);for(int i=0;i<5;i++){v+=a*n(p);p=m*p;a*=.5;}return v;}
void main(){
  vec2 s=gl_FragCoord.xy/R; vec2 uv=gl_FragCoord.xy/R.y; float t=T*.016;
  vec2 p=uv*vec2(1.25,1.6)+vec2(3.1,1.7);
  vec2 q=vec2(fbm(p+vec2(0.,t)),fbm(p+vec2(5.2,1.3)-t*.8));
  vec2 w=vec2(fbm(p+3.2*q+vec2(1.7,9.2)+t*.6),fbm(p+3.2*q+vec2(8.3,2.8)-t*.5));
  float f=fbm(p+2.6*w);
  float ridge=1.-abs(f*2.-1.);
  float vein=pow(ridge,20.);
  float soft=pow(ridge,4.);
  float body=smoothstep(.45,.85,f)*smoothstep(.2,.9,length(w));
  vec3 c=STONE;
  c+=BODY*body;
  c+=GLOW*soft*.3*body;
  c+=VEIN*vein*(.2+.7*body);
  float e=.015; vec2 P=p*.5+vec2(t*.5,-t*.35);
  vec2 g=vec2(fbm(P+vec2(e,0.))-fbm(P-vec2(e,0.)),fbm(P+vec2(0.,e))-fbm(P-vec2(0.,e)))/(2.*e);
  vec3 N=normalize(vec3(-g*.9,1.)); vec3 Hv=normalize(normalize(vec3(-.45,.55,.7))+vec3(0.,0.,1.));
  float sp=pow(max(dot(N,Hv),0.),36.);
  c+=mix(SHEEN,WARM,body)*sp*.10;
  float vig=smoothstep(1.35,.15,length((s-vec2(.62,.95))*vec2(1.,1.35)));
  c*=.38+.72*vig;
  c+=(h(gl_FragCoord.xy+fract(T))-.5)/255.;
  gl_FragColor=vec4(c,1.);
}`;
const colours = { STONE: "--tx-home-stone", BODY: "--tx-home-stone-body", GLOW: "--tx-home-stone-glow", VEIN: "--tx-home-stone-vein",
  SHEEN: "--tx-home-stone-sheen", WARM: "--tx-home-stone-sheen-warm" };
const SCALE = 0.5, FRAME_MS = 33;

/** "#RRGGBB" as 0–1 channels. */
const rgb = (hex: string) => [1, 3, 5].map(at => parseInt(hex.trim().slice(at, at + 2), 16) / 255) as [number, number, number];

export function HomeStone() {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const element = canvas.current!;
    const gl = element.getContext("webgl", { antialias: false, alpha: false, preserveDrawingBuffer: true, powerPreference: "low-power" });
    if (!gl) return;
    const program = gl.createProgram()!;
    for (const [kind, source] of [[gl.VERTEX_SHADER, vertex], [gl.FRAGMENT_SHADER, fragment]] as const) {
      const shader = gl.createShader(kind)!;
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      gl.attachShader(program, shader);
    }
    gl.linkProgram(program);
    gl.useProgram(program);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, "p");
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    for (const [uniform, token] of Object.entries(colours)) gl.uniform3f(gl.getUniformLocation(program, uniform), ...rgb(tokenValue(token)));
    const size = gl.getUniformLocation(program, "R"), time = gl.getUniformLocation(program, "T");
    const t0 = performance.now() - 90_000;
    const draw = (ms: number) => { gl.uniform1f(time, (ms - t0) / 1000); gl.drawArrays(gl.TRIANGLES, 0, 3); };

    let frame = 0, previous = 0, last = performance.now(), onScreen = false;
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const loop = (ms: number) => {
      frame = requestAnimationFrame(loop);
      if (ms - previous < FRAME_MS) return;
      previous = last = ms;
      draw(ms);
    };
    const update = () => {
      const run = onScreen && !still && document.visibilityState === "visible";
      if (run && !frame) frame = requestAnimationFrame(loop);
      if (!run && frame) { cancelAnimationFrame(frame); frame = 0; }
    };
    const resize = () => {
      const width = Math.max(1, Math.round(element.clientWidth * SCALE)), height = Math.max(1, Math.round(element.clientHeight * SCALE));
      if (width === element.width && height === element.height) return;
      element.width = width; element.height = height;
      gl.viewport(0, 0, width, height);
      gl.uniform2f(size, width, height);
      draw(last);
    };
    // Hidden under an open workspace or another screen (display: none), the canvas leaves the viewport.
    const visible = new IntersectionObserver(([entry]) => { onScreen = entry.isIntersecting; if (onScreen) resize(); update(); });
    const sized = new ResizeObserver(resize);
    visible.observe(element);
    sized.observe(element);
    resize();
    draw(last);
    document.addEventListener("visibilitychange", update);
    return () => {
      cancelAnimationFrame(frame);
      visible.disconnect();
      sized.disconnect();
      document.removeEventListener("visibilitychange", update);
    };
  }, []);
  return <canvas ref={canvas} className="talyx-home-stone" aria-hidden="true" />;
}
