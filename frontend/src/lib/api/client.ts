const API='/api/v1'; let token:string|null=null;
export function setToken(value:string|null){token=value}
function csrfToken(){return document.cookie.split('; ').find(v=>v.startsWith('kavach_csrf='))?.split('=').slice(1).join('=')}
export async function request<T>(path:string,init:RequestInit={}):Promise<T>{
 const headers=new Headers(init.headers); if(!(init.body instanceof FormData))headers.set('Content-Type','application/json'); if(token)headers.set('Authorization',`Bearer ${token}`); const csrf=csrfToken(); if(csrf)headers.set('X-CSRF-Token',decodeURIComponent(csrf));
 const res=await fetch(`${API}${path}`,{...init,headers,credentials:'include'});
 if(!res.ok){let message='Request failed';try{message=(await res.json()).detail??message}catch{} throw new Error(message)}
 return res.status===204?undefined as T:res.json();
}
export async function download(path:string,filename:string){
 const headers=new Headers();if(token)headers.set('Authorization',`Bearer ${token}`);
 const res=await fetch(`${API}${path}`,{headers,credentials:'include'});
 if(!res.ok){let message='Download failed';try{message=(await res.json()).detail??message}catch{}throw new Error(message)}
 const url=URL.createObjectURL(await res.blob());const link=document.createElement('a');link.href=url;link.download=filename;link.click();URL.revokeObjectURL(url);
}
