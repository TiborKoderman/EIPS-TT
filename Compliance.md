make sure that the program really does this as well, the delay per ip works, but the robots could specify a different one, I think we could just solve this by robots if descovering a site with a higher delay they should report it to the server and the server should set the time outs while considering this

\section{Robots.txt and politeness}
The crawler enforces robots and ethics constraints before each fetch:
\begin{itemize}
    \item robots fetched per origin and cached,
    \item \texttt{Allow}/\texttt{Disallow} checked for crawler user-agent,
    \item \texttt{Crawl-delay} read when present,
    \item \texttt{Sitemap} URLs collected for optional frontier seeding,
    \item hard minimum delay of 5 seconds per target IP, even if robots does not define delay.
\end{itemize}

The implementation uses a thread-safe per-IP limiter. Effective delay is:
\[
    \text{effective\_delay} = \max(5s, \text{robots\_crawl\_delay})
\]