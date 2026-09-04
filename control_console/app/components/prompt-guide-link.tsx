export default function PromptGuideLink({ section }: { section: string }) {
  return <a className="prompt-guide-link" href={`/content/guide#${section}`}>填写规范</a>;
}

