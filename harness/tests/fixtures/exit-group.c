/* Test only: a zombie group leader with one deliberately live sibling thread. */
#include <pthread.h>
#include <stdio.h>
#include <unistd.h>

static void *finish_after_release(void *unused) {
    (void)unused;
    char byte;
    (void)read(STDIN_FILENO, &byte, 1);
    return NULL;
}
int main(void) {
    puts("READY");
    fflush(stdout);
    char byte;
    if (read(STDIN_FILENO, &byte, 1) != 1) return 2;
    pthread_t thread;
    if (pthread_create(&thread, NULL, finish_after_release, NULL) != 0) return 3;
    pthread_exit(NULL);
}
