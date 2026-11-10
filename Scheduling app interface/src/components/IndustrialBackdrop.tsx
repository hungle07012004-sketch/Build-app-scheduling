import {useEffect, useRef} from 'react';

/** Decorative movement only; timeline geometry and application state stay stable. */
export default function IndustrialBackdrop() {
  const ref=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    const shell=ref.current?.parentElement;
    if(!shell) return;
    const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
    let frame=0;
    const move=(event:PointerEvent)=>{
      if(reduced.matches || event.pointerType!=='mouse') return;
      cancelAnimationFrame(frame);
      frame=requestAnimationFrame(()=>{
        ref.current?.style.setProperty('--parallax-x',`${(event.clientX/window.innerWidth-.5)*10}px`);
        ref.current?.style.setProperty('--parallax-y',`${(event.clientY/window.innerHeight-.5)*8}px`);
        const card=(event.target as Element).closest<HTMLElement>('.aps-panel, .aps-metric');
        if(card){const rect=card.getBoundingClientRect();card.style.setProperty('--spot-x',`${event.clientX-rect.left}px`);card.style.setProperty('--spot-y',`${event.clientY-rect.top}px`);}
      });
    };
    shell.addEventListener('pointermove',move,{passive:true});
    return()=>{cancelAnimationFrame(frame);shell.removeEventListener('pointermove',move);};
  },[]);
  return <div ref={ref} className="industrial-backdrop" aria-hidden="true"/>;
}
