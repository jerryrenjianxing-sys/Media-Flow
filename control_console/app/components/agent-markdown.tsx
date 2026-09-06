import { Fragment, type ReactNode } from "react";

// A deliberately small Markdown renderer: React escapes all content; no raw HTML,
// images, embedded frames or executable URL schemes from model output.
function inline(text:string):ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^\s)]+\))/g).map((part,i)=>{
    if(part.startsWith("`")&&part.endsWith("`"))return <code key={i}>{part.slice(1,-1)}</code>;
    if(part.startsWith("**")&&part.endsWith("**"))return <strong key={i}>{part.slice(2,-2)}</strong>;
    const link=part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if(link&&(/^(https?:\/\/)/i.test(link[2])||/^\/(?!\/)/.test(link[2])))return <a key={i} href={link[2]} target="_blank" rel="noopener noreferrer">{link[1]}</a>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}
export default function AgentMarkdown({text}:{text:string}) {
  return <div className="agent-markdown">{text.split(/(```[\s\S]*?(?:```|$))/g).map((chunk,index)=>{
    if(chunk.startsWith("```")){const lines=chunk.slice(3).replace(/```$/,"").split("\n");const language=lines.shift();return <div className="agent-code" key={index}><small>{language||"代码"}</small><pre><code>{lines.join("\n")}</code></pre></div>;}
    return chunk.split(/\n\s*\n/).filter(Boolean).map((block,j)=>{
      const lines=block.trim().split("\n");
      if(lines.length>1&&/^\|?[\s:|-]+\|[\s:|-]+$/.test(lines[1])){
        const cells=(line:string)=>line.replace(/^\||\|$/g,"").split("|");
        return <div className="agent-table-wrap" key={`${index}-${j}`}><table><thead><tr>{cells(lines[0]).map((c,k)=><th key={k}>{inline(c.trim())}</th>)}</tr></thead><tbody>{lines.slice(2).map((l,k)=><tr key={k}>{cells(l).map((c,n)=><td key={n}>{inline(c.trim())}</td>)}</tr>)}</tbody></table></div>;
      }
      if(lines.every(l=>/^\s*[-*]\s/.test(l)))return <ul key={`${index}-${j}`}>{lines.map((l,k)=><li key={k}>{inline(l.replace(/^\s*[-*]\s/,""))}</li>)}</ul>;
      if(lines.every(l=>/^\s*\d+\.\s/.test(l)))return <ol key={`${index}-${j}`}>{lines.map((l,k)=><li key={k}>{inline(l.replace(/^\s*\d+\.\s/,""))}</li>)}</ol>;
      return <div key={`${index}-${j}`}>{lines.map((l,k)=>/^#{1,6}\s/.test(l)?<h3 key={k}>{inline(l.replace(/^#{1,6}\s/,""))}</h3>:<p key={k}>{inline(l)}</p>)}</div>;
    });
  })}</div>;
}
