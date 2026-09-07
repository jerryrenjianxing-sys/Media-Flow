import {redirect} from "next/navigation";
import {NATIVE_CHAT_URL} from "./brand";
import ManagementPage from "./manage/page";

// Legacy bookmarks remain usable, without mounting a second chat frontend.
export default async function ConsoleHome({searchParams}:{searchParams:Promise<Record<string,string|string[]|undefined>>}) {
  const query=await searchParams;
  if(typeof query.settings==="string")redirect("/settings");
  if(typeof query.session==="string")redirect(`${NATIVE_CHAT_URL}?session=${encodeURIComponent(query.session)}`);
  return <ManagementPage/>;
}
