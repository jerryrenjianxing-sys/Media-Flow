import {redirect} from "next/navigation";
import PlatformHome from "./components/platform-home";

// Legacy bookmarks remain local; no chat redirect or history mutation.
export default async function ConsoleHome({searchParams}:{searchParams:Promise<Record<string,string|string[]|undefined>>}) {
  const query=await searchParams;
  if(typeof query.settings==="string")redirect("/settings");
  return <PlatformHome legacySession={typeof query.session==="string"}/>;
}
