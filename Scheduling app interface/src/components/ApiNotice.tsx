export default function ApiNotice({error, children}: {error?:string;children?:React.ReactNode}) {
  return error ? <div role="alert" className="m-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}{children}</div> : null;
}
