#include "player_logger.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifdef _WIN32
#include <winsock2.h>
typedef SOCKET UdpSocket;
#define CLOSE_SOCKET closesocket
#define INVALID_UDP  INVALID_SOCKET
#else
#include <arpa/inet.h>
#include <netdb.h>
#include <sys/socket.h>
#include <unistd.h>
typedef int UdpSocket;
#define CLOSE_SOCKET close
#define INVALID_UDP  (-1)
#endif

#include "../../../scr_server/sensors.h"
#include "../../../scr_server/ObstacleSensors.h"

/* ── 常量 ────────────────────────────────────────────────── */
enum {
    MAX_PLAYERS  = 10,
    TRACK_COUNT  = 19,
    OPP_COUNT    = 36,
    FOCUS_COUNT  = 5,
    PACKET_SIZE  = 8192
};

/* ── 状态结构体 ──────────────────────────────────────────── */
struct LogState {
    FILE          *file;
    FILE          *rankingFile;
    UdpSocket      socket;
    sockaddr_in    peer;
    Sensors       *trackSens;
    ObstacleSensors *oppSens;
    Sensors       *focusSens;
    tTrack        *track;
    double         nextSample;
    double         period;
    double         prevDist;
    double         distRaced;
    unsigned long  seq;
    int            active;
    int            socketOpen;
};

static LogState states[MAX_PLAYERS];
static int networkReady = 0;

/* ── 工具函数 ─────────────────────────────────────────────── */

static const char *envOr(const char *n, const char *d)
{
    const char *v = getenv(n);
    return (v && v[0]) ? v : d;
}

static void closeState(LogState *s)
{
    if (s->file) {
        fflush(s->file);
        fclose(s->file);
    }
    if (s->rankingFile) {
        fflush(s->rankingFile);
        fclose(s->rankingFile);
    }
    if (s->socketOpen)
        CLOSE_SOCKET(s->socket);
    delete s->trackSens;
    delete s->oppSens;
    delete s->focusSens;
    memset(s, 0, sizeof(*s));
}

static void openUdp(LogState *s)
{
    if (!networkReady) {
#ifdef _WIN32
        WSADATA d;
        if (WSAStartup(MAKEWORD(2, 2), &d) != 0)
            return;
#endif
        networkReady = 1;
    }

    s->socket = socket(AF_INET, SOCK_DGRAM, 0);
    if (s->socket == INVALID_UDP)
        return;
    s->socketOpen = 1;

    memset(&s->peer, 0, sizeof(s->peer));
    s->peer.sin_family = AF_INET;
    s->peer.sin_port   = htons((unsigned short)atoi(envOr("TORCS_PLAYER_UDP_PORT", "3101")));

    const char *host = envOr("TORCS_PLAYER_UDP_HOST", "127.0.0.1");
    s->peer.sin_addr.s_addr = inet_addr(host);

    if (s->peer.sin_addr.s_addr == INADDR_NONE) {
        hostent *e = gethostbyname(host);
        if (!e) {
            CLOSE_SOCKET(s->socket);
            s->socketOpen = 0;
            return;
        }
        memcpy(&s->peer.sin_addr, e->h_addr, e->h_length);
    }
}

static void headerArray(FILE *f, const char *name, int count)
{
    for (int i = 0; i < count; ++i)
        fprintf(f, ",%s_%d", name, i);
}

static void add(char *buf, size_t cap, int *len, double value)
{
    if (*len < 0 || (size_t)*len >= cap)
        return;
    int n = snprintf(buf + *len, cap - *len, ",%.6f", value);
    if (n > 0)
        *len += n;
}

static void csvName(FILE *f, const char *name)
{
    fputc('"', f);
    for (const char *p = name ? name : ""; *p; ++p) {
        if (*p == '"')
            fputc('"', f);
        fputc(*p, f);
    }
    fputc('"', f);
}

/* ── 排名写入 ─────────────────────────────────────────────── */

static void writeRankings(LogState *s,const tSituation *sit){
    if(!s->rankingFile)return;
    for(int i=0;i<sit->_ncars;++i){
        const tCarElt *c=sit->cars[i];if(!c)continue;
        fprintf(s->rankingFile,"%.6f,%d,",sit->currentTime,i);
        csvName(s->rankingFile,c->_name);
        fprintf(s->rankingFile,",%d,%d,%.6f\n",c->race.pos,c->_laps,c->race.distFromStartLine);

        // 新增：构造 UDP 包并发送
        char rbuf[512];
        int rlen=snprintf(rbuf,sizeof(rbuf),"R,%.6f,%d,%s,%d,%d,%.6f\n",
            sit->currentTime,i,c->_name?c->_name:"",
            c->race.pos,c->_laps,c->race.distFromStartLine);
        if(rlen>0&&s->socketOpen)
            sendto(s->socket,rbuf,rlen,0,(const sockaddr*)&s->peer,sizeof(s->peer));
    }
    if((s->seq%20)==0)fflush(s->rankingFile);
}

/* ── 公开接口 ─────────────────────────────────────────────── */

void PlayerLoggerStop(int p)
{
    if (p >= 1 && p <= MAX_PLAYERS)
        closeState(&states[p - 1]);
}

void PlayerLoggerStart(int p, tTrack *track, tCarElt *car, tSituation *sit)
{
    if (p < 1 || p > MAX_PLAYERS || !track || !car || !sit)
        return;

    LogState *s = &states[p - 1];
    closeState(s);
    s->active   = 1;
    s->track    = track;
    s->prevDist = -1;

    double hz = atof(envOr("TORCS_PLAYER_LOG_HZ", "20"));
    s->period = (hz > 0) ? 1.0 / hz : 0.05;

    /* 初始化传感器 */
    s->trackSens = new Sensors(car, TRACK_COUNT);
    s->focusSens = new Sensors(car, FOCUS_COUNT);
    s->oppSens   = new ObstacleSensors(OPP_COUNT, track, car, sit, 200);

    for (int i = 0; i < TRACK_COUNT; ++i)
        s->trackSens->setSensor(i, -90.0f + 10.0f * i, 200);
    for (int i = 0; i < FOCUS_COUNT; ++i)
        s->focusSens->setSensor(i, -2.0f + i, 200);

    /* 主数据 CSV */
    char path[1024];
#ifdef _WIN32
    snprintf(path, sizeof(path), "%s\\player-%d-%ld.csv",
             envOr("TORCS_PLAYER_LOG_DIR", "."), p, (long)time(NULL));
#else
    snprintf(path, sizeof(path), "%s/player-%d-%ld.csv",
             envOr("TORCS_PLAYER_LOG_DIR", "."), p, (long)time(NULL));
#endif
    s->file = fopen(path, "w");
    if (s->file) {
        fputs("seq,sim_time,player,lap,x,y,yaw,accel_x,accel_y,"
              "steer,throttle,brake,clutch,angle,curLapTime,damage,"
              "distFromStart,distRaced,fuel,gear,lastLapTime,racePos,"
              "rpm,speedX,speedY,speedZ,trackPos,z", s->file);
        headerArray(s->file, "opponent",    OPP_COUNT);
        headerArray(s->file, "track",       TRACK_COUNT);
        headerArray(s->file, "wheelSpinVel", 4);
        headerArray(s->file, "focus",       FOCUS_COUNT);
        fputc('\n', s->file);
        fflush(s->file);
    }

    /* 排名 CSV */
#ifdef _WIN32
    snprintf(path, sizeof(path), "%s\\rankings-player-%d-%ld.csv",
             envOr("TORCS_PLAYER_LOG_DIR", "."), p, (long)time(NULL));
#else
    snprintf(path, sizeof(path), "%s/rankings-player-%d-%ld.csv",
             envOr("TORCS_PLAYER_LOG_DIR", "."), p, (long)time(NULL));
#endif
    s->rankingFile = fopen(path, "w");
    if (s->rankingFile) {
        fputs("sim_time,car_index,car_name,race_pos,laps,dist_from_start\n",
              s->rankingFile);
        fflush(s->rankingFile);
    }

    openUdp(s);
}

void PlayerLoggerSample(int p, const tCarElt *car, const tSituation *sit)
{
    if (p < 1 || p > MAX_PLAYERS || !car || !sit)
        return;

    LogState *s = &states[p - 1];
    if (!s->active || !s->track)
        return;
    if (sit->currentTime + 1e-9 < s->nextSample)
        return;
    s->nextSample = sit->currentTime + s->period;

    /* 计算衍生量 */
    float trackPos = 2.0f * car->_trkPos.toMiddle / car->_trkPos.seg->width;
    float angle    = RtTrackSideTgAngleL((tTrkLocPos *)&car->_trkPos) - car->_yaw;
    NORM_PI_PI(angle);

    /* 更新传感器 */
    float track[TRACK_COUNT], focus[FOCUS_COUNT], opp[OPP_COUNT];
    if (trackPos >= -1 && trackPos <= 1) {
        s->trackSens->sensors_update();
        s->focusSens->sensors_update();
        for (int i = 0; i < TRACK_COUNT; ++i)
            track[i] = s->trackSens->getSensorOut(i);
        for (int i = 0; i < FOCUS_COUNT; ++i)
            focus[i] = s->focusSens->getSensorOut(i);
    } else {
        for (int i = 0; i < TRACK_COUNT; ++i) track[i] = -1;
        for (int i = 0; i < FOCUS_COUNT; ++i) focus[i] = -1;
    }

    s->oppSens->sensors_update((tSituation *)sit);
    for (int i = 0; i < OPP_COUNT; ++i)
        opp[i] = s->oppSens->getObstacleSensorOut(i);

    /* 累计行驶距离 */
    double distance = car->race.distFromStartLine;
    if (s->prevDist < 0)
        s->prevDist = distance;
    double delta = distance - s->prevDist;
    s->prevDist  = distance;
    if (delta >  100) delta -= s->track->length;
    if (delta < -100) delta += s->track->length;
    s->distRaced += delta;

    /* 构造数据包 */
    char buf[PACKET_SIZE];
    int len = snprintf(buf, sizeof(buf),
                       "%lu,%.6f,%d,%d,"
                       "%.6f,%.6f,%.6f,%.6f,%.6f,"
                       "%.6f,%.6f,%.6f,%.6f,"
                       "%.6f,%.6f,%d,"
                       "%.6f,%.6f,%.6f,%d,%.6f,%d,"
                       "%.6f,%.6f,%.6f,%.6f,%.6f,%.6f",
                       s->seq++, sit->currentTime, p, car->_laps,
                       car->_pos_X, car->_pos_Y, car->_yaw,
                       car->_accel_x, car->_accel_y,
                       car->_steerCmd, car->_accelCmd, car->_brakeCmd, car->_clutchCmd,
                       angle, car->_curLapTime, car->_dammage,
                       distance, s->distRaced, car->_fuel, car->_gear,
                       car->_lastLapTime, car->race.pos,
                       car->_enginerpm * 10,
                       car->_speed_x * 3.6, car->_speed_y * 3.6, car->_speed_z * 3.6,
                       trackPos,
                       car->_pos_Z - RtTrackHeightL((tTrkLocPos *)&car->_trkPos));

    for (int i = 0; i < OPP_COUNT;   ++i) add(buf, sizeof(buf), &len, opp[i]);
    for (int i = 0; i < TRACK_COUNT; ++i) add(buf, sizeof(buf), &len, track[i]);
    for (int i = 0; i < 4;           ++i) add(buf, sizeof(buf), &len, car->_wheelSpinVel(i));
    for (int i = 0; i < FOCUS_COUNT; ++i) add(buf, sizeof(buf), &len, focus[i]);

    if (len <= 0 || (size_t)len + 1 >= sizeof(buf))
        return;
    buf[len++] = '\n';
    buf[len]   = 0;

    /* 写文件 + UDP 发送 */
    if (s->file) {
        fwrite(buf, 1, len, s->file);
        if ((s->seq % 20) == 0)
            fflush(s->file);
    }
    if (s->socketOpen)
        sendto(s->socket, buf, len, 0, (const sockaddr *)&s->peer, sizeof(s->peer));

    writeRankings(s, sit);
}
