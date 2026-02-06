import { useEffect, useState } from 'react';
import { systemAPI, extractDataFromResponse } from '../services/api';

export default function useVersion() {
  const [version, setVersion] = useState(null);
  const [gitSha, setGitSha] = useState(null);

  useEffect(() => {
    let cancelled = false;
    systemAPI
      .getVersion()
      .then((res) => {
        const data = extractDataFromResponse(res);
        if (!cancelled) {
          setVersion(data?.version || null);
          setGitSha(data?.git_sha || null);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const displayVersion = version ? `v${version}${gitSha ? ' • ' + gitSha.slice(0, 7) : ''}` : '';
  return { version, gitSha, displayVersion };
}
