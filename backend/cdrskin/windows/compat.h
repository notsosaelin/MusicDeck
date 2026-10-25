/*
 * The POSIX pieces libburn and cdrskin use that MinGW doesn't have, for building them as a native
 * Windows program (libcdio as the SCSI transport). Force-included (-include compat.h) into every file,
 * so libburn's source stays as released. None of them is on the path MusicDeck burns by:
 *
 * - signals: only cdrskin's abort handlers name them; Windows delivers SIGINT, SIGTERM, SIGABRT...
 * - fcntl, kill, alarm: drive locking and abort cleanup.
 * - pipe, select: cdrskin's fifo, which MusicDeck turns off (fs=0) on Windows.
 * - uid, lstat, readlink: Linux device-file checks (no symlinks to follow, nobody is root).
 */
#ifndef MUSICDECK_CDRSKIN_COMPAT_H
#define MUSICDECK_CDRSKIN_COMPAT_H
#ifdef _WIN32

#include <signal.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <winsock2.h>  /* fd_set, select, struct timeval */

#define SIGHUP 1
#define SIGQUIT 3
#define SIGKILL 9
#define SIGUSR1 10
#define SIGUSR2 12
#define SIGPIPE 13
#define SIGALRM 14
#define SIGCHLD 17
#define SIGCONT 18
#define SIGSTOP 19
#define SIGTSTP 20
#define SIGTTIN 21
#define SIGTTOU 22
#define SIGXCPU 24

typedef unsigned long sigset_t;
struct sigaction {
	void (*sa_handler)(int);
	sigset_t sa_mask;
	int sa_flags;
};
int sigaction(int signum, const struct sigaction *act, struct sigaction *oldact);
int sigfillset(sigset_t *set);
int sigdelset(sigset_t *set, int signum);
int sigemptyset(sigset_t *set);
int sigaddset(sigset_t *set, int signum);
/* pthread_sigmask: winpthreads has it (a no-op) */

#define F_GETFL 3
#define F_SETFL 4
#define F_SETLK 6
#define F_WRLCK 1
#define F_UNLCK 2
int fcntl(int fd, int cmd, ...);
int kill(int pid, int sig);
unsigned int alarm(unsigned int seconds);
int pipe(int fds[2]);
int fsync(int fd);

#ifndef S_IFLNK
#define S_IFLNK 0xA000
#endif
#ifndef S_IFSOCK
#define S_IFSOCK 0xC000
#endif
#define lstat stat
#define st_blocks st_size / 512
int readlink(const char *path, char *buf, size_t size);
unsigned int getuid(void);
unsigned int geteuid(void);

#endif /* _WIN32 */
#endif
