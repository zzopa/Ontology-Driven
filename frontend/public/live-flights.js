/**
 * live-flights.js
 * 
 * 3D 实时航班数字孪生前端模块（Three.js 0.182 无侵入式集成）
 * 实现飞常准风格的 3D 实时起降、航迹回显、标牌追踪与机位/廊桥联动。
 */

// 预置常见航司配色、全称与标志代码
const AIRLINE_INFO = {
    CX: { name: '国泰航空', full: '国泰航空公司 (Cathay Pacific)', color: '#006564', tail: 0x006564 },
    UO: { name: '香港快运', full: '香港快运航空 (HK Express)', color: '#7c3aed', tail: 0x7c3aed },
    HX: { name: '香港航空', full: '香港航空公司 (Hong Kong Airlines)', color: '#dc2626', tail: 0xdc2626 },
    CZ: { name: '南方航空', full: '中国南方航空 (China Southern)', color: '#0284c7', tail: 0x0284c7 },
    MU: { name: '东方航空', full: '中国东方航空 (China Eastern)', color: '#2563eb', tail: 0x2563eb },
    CA: { name: '中国国航', full: '中国国际航空 (Air China)', color: '#dc2626', tail: 0xdc2626 },
    HU: { name: '海南航空', full: '海南航空 (Hainan Airlines)', color: '#ef4444', tail: 0xef4444 },
    ZH: { name: '深圳航空', full: '深圳航空 (Shenzhen Airlines)', color: '#dc2626', tail: 0xdc2626 },
    MF: { name: '厦门航空', full: '厦门航空 (XiamenAir)', color: '#0284c7', tail: 0x0284c7 },
    '3U': { name: '四川航空', full: '四川航空 (Sichuan Airlines)', color: '#dc2626', tail: 0xdc2626 },
    '9C': { name: '春秋航空', full: '春秋航空 (Spring Airlines)', color: '#16a34a', tail: 0x16a34a },
    HO: { name: '吉祥航空', full: '吉祥航空 (Juneyao Air)', color: '#9d174d', tail: 0x831843 },
    SQ: { name: '新加坡航空', full: '新加坡航空公司 (Singapore Airlines)', color: '#1e3a8a', tail: 0x1e3a8a },
    JL: { name: '日本航空', full: '日本航空 (Japan Airlines)', color: '#dc2626', tail: 0xdc2626 },
    NH: { name: '全日空', full: '全日空航空 (ANA)', color: '#0284c7', tail: 0x0284c7 },
    EK: { name: '阿联酋航空', full: '阿联酋航空 (Emirates)', color: '#dc2626', tail: 0xdc2626 },
    QR: { name: '卡塔尔航空', full: '卡塔尔航空 (Qatar Airways)', color: '#831843', tail: 0x831843 },
    LH: { name: '汉莎航空', full: '德国汉莎航空 (Lufthansa)', color: '#1e3a8a', tail: 0x1e3a8a },
    default: { name: '民航客机', full: '民航客运定期航班', color: '#0284c7', tail: 0x0284c7 },
};

// 预置主流机场 IATA 代码与中英文名称全景字典
const AIRPORT_NAMES = {
    PEK: { name: '北京首都', full: '北京首都国际机场 (PEK)', city: '北京' },
    PKX: { name: '北京大兴', full: '北京大兴国际机场 (PKX)', city: '北京' },
    PVG: { name: '上海浦东', full: '上海浦东国际机场 (PVG)', city: '上海' },
    SHA: { name: '上海虹桥', full: '上海虹桥国际机场 (SHA)', city: '上海' },
    CAN: { name: '广州白云', full: '广州白云国际机场 (CAN)', city: '广州' },
    SZX: { name: '深圳宝安', full: '深圳宝安国际机场 (SZX)', city: '深圳' },
    CTU: { name: '成都双流', full: '成都双流国际机场 (CTU)', city: '成都' },
    TFU: { name: '成都天府', full: '成都天府国际机场 (TFU)', city: '成都' },
    CKG: { name: '重庆江北', full: '重庆江北国际机场 (CKG)', city: '重庆' },
    XIY: { name: '西安咸阳', full: '西安咸阳国际机场 (XIY)', city: '西安' },
    KMG: { name: '昆明长水', full: '昆明长水国际机场 (KMG)', city: '昆明' },
    HGH: { name: '杭州萧山', full: '杭州萧山国际机场 (HGH)', city: '杭州' },
    NKG: { name: '南京禄口', full: '南京禄口国际机场 (NKG)', city: '南京' },
    XMN: { name: '厦门高崎', full: '厦门高崎国际机场 (XMN)', city: '厦门' },
    WUH: { name: '武汉天河', full: '武汉天河国际机场 (WUH)', city: '武汉' },
    CGO: { name: '郑州新郑', full: '郑州新郑国际机场 (CGO)', city: '郑州' },
    CSX: { name: '长沙黄花', full: '长沙黄花国际机场 (CSX)', city: '长沙' },
    TAO: { name: '青岛胶东', full: '青岛胶东国际机场 (TAO)', city: '青岛' },
    DLC: { name: '大连周水子', full: '大连周水子国际机场 (DLC)', city: '大连' },
    SYX: { name: '三亚凤凰', full: '三亚凤凰国际机场 (SYX)', city: '三亚' },
    HAK: { name: '海口美兰', full: '海口美兰国际机场 (HAK)', city: '海口' },
    URC: { name: '乌鲁木齐', full: '乌鲁木齐地窝堡国际机场 (URC)', city: '乌鲁木齐' },
    HKG: { name: '香港国际', full: '香港国际机场 (HKG)', city: '香港' },
    MFM: { name: '澳门国际', full: '澳门国际机场 (MFM)', city: '澳门' },
    TPE: { name: '台北桃园', full: '台湾桃园国际机场 (TPE)', city: '台北' },
    HND: { name: '东京羽田', full: '东京羽田国际机场 (HND)', city: '东京' },
    NRT: { name: '东京成田', full: '东京成田国际机场 (NRT)', city: '东京' },
    ICN: { name: '首尔仁川', full: '首尔仁川国际机场 (ICN)', city: '首尔' },
    SIN: { name: '新加坡樟宜', full: '新加坡樟宜国际机场 (SIN)', city: '新加坡' },
    BKK: { name: '曼谷素万那普', full: '曼谷素万那普国际机场 (BKK)', city: '曼谷' },
    LHR: { name: '伦敦希思罗', full: '伦敦希思罗国际机场 (LHR)', city: '伦敦' },
    CDG: { name: '巴黎戴高乐', full: '巴黎戴高乐国际机场 (CDG)', city: '巴黎' },
    FRA: { name: '法兰克福', full: '法兰克福国际机场 (FRA)', city: '法兰克福' },
    DXB: { name: '迪拜国际', full: '迪拜国际机场 (DXB)', city: '迪拜' },
    JFK: { name: '纽约肯尼迪', full: '纽约肯尼迪国际机场 (JFK)', city: '纽约' },
    LAX: { name: '洛杉矶国际', full: '洛杉矶国际机场 (LAX)', city: '洛杉矶' },
};

// 预置主流机型全称与宽窄体规格字典
const AIRCRAFT_MODELS = {
    B789: { name: '波音 787-9 梦想客机', desc: '双通道大型远程宽体客机', cat: '双通道宽体' },
    B788: { name: '波音 787-8 梦想客机', desc: '双通道远程宽体客机', cat: '双通道宽体' },
    B77W: { name: '波音 777-300ER', desc: '大型远程超宽体客机', cat: '重型宽体' },
    B772: { name: '波音 777-200', desc: '双通道远程宽体客机', cat: '双通道宽体' },
    B738: { name: '波音 737-800', desc: '主流单通道干线客机', cat: '单通道窄体' },
    B739: { name: '波音 737-900', desc: '加长型单通道干线客机', cat: '单通道窄体' },
    B38M: { name: '波音 737 MAX 8', desc: '新一代高效单通道客机', cat: '单通道窄体' },
    A359: { name: '空客 A350-900', desc: '新一代超静音超宽体客机', cat: '远程宽体' },
    A333: { name: '空客 A330-300', desc: '双通道中远程宽体客机', cat: '双通道宽体' },
    A332: { name: '空客 A330-200', desc: '双通道远程宽体客机', cat: '双通道宽体' },
    A321: { name: '空客 A321-200 / neo', desc: '高载客单通道干线客机', cat: '单通道窄体' },
    A320: { name: '空客 A320-200 / neo', desc: '主流单通道干线客机', cat: '单通道窄体' },
    A319: { name: '空客 A319', desc: '高原性能单通道客机', cat: '单通道窄体' },
    ARJ21: { name: '中国商飞 ARJ21-700', desc: '国产支线喷气客机', cat: '国产支线' },
    C919: { name: '中国商飞 C919', desc: '国产新一代大型干线客机', cat: '单通道干线' },
};

// 预置全场景统一飞行状态颜色标准与动力学规范
const FLIGHT_STATUS_CONFIG = {
    APPROACH: {
        code: 'APPROACH',
        label: '五边进近',
        fullLabel: '🔵 五边进近下滑 (APPROACH)',
        color: '#38bdf8',       // 科技青蓝
        hexColor: 0x38bdf8,
        bgRgba: 'rgba(56, 189, 248, 0.2)',
        borderRgba: 'rgba(56, 189, 248, 0.7)',
        glowRgba: 'rgba(56, 189, 248, 0.45)',
        icon: '🔵',
        desc: '建立五边盲降航道，沿 3° 下滑线稳定进近',
        tagClass: 'status-tag-approach',
    },
    CLIMB: {
        code: 'CLIMB',
        label: '离港爬升',
        fullLabel: '🟠 离港爬升 (CLIMB)',
        color: '#f59e0b',       // 琥珀金橙
        hexColor: 0xf59e0b,
        bgRgba: 'rgba(245, 158, 11, 0.2)',
        borderRgba: 'rgba(245, 158, 11, 0.7)',
        glowRgba: 'rgba(245, 158, 11, 0.45)',
        icon: '🟠',
        desc: '起飞离港爬升中，按标准仪表离场程序 (SID) 爬升',
        tagClass: 'status-tag-climb',
    },
    CRUISE: {
        code: 'CRUISE',
        label: '巡航平飞',
        fullLabel: '🟢 巡航平飞 (CRUISE)',
        color: '#10b981',       // 翡翠草绿
        hexColor: 0x10b981,
        bgRgba: 'rgba(16, 185, 129, 0.2)',
        borderRgba: 'rgba(16, 185, 129, 0.7)',
        glowRgba: 'rgba(16, 185, 129, 0.45)',
        icon: '🟢',
        desc: '巡航高度层稳定平飞，由航路管制雷达监控',
        tagClass: 'status-tag-cruise',
    },
    EN_ROUTE: {
        code: 'EN_ROUTE',
        label: '巡航平飞',
        fullLabel: '🟢 巡航平飞 (CRUISE)',
        color: '#10b981',
        hexColor: 0x10b981,
        bgRgba: 'rgba(16, 185, 129, 0.2)',
        borderRgba: 'rgba(16, 185, 129, 0.7)',
        glowRgba: 'rgba(16, 185, 129, 0.45)',
        icon: '🟢',
        desc: '处于巡航高度层平飞，航路巡航中',
        tagClass: 'status-tag-cruise',
    },
    LANDED: {
        code: 'LANDED',
        label: '跑道滑跑',
        fullLabel: '🛬 跑道滑跑 (ROLLOUT)',
        color: '#34d399',       // 薄荷青绿
        hexColor: 0x34d399,
        bgRgba: 'rgba(52, 211, 153, 0.2)',
        borderRgba: 'rgba(52, 211, 153, 0.7)',
        glowRgba: 'rgba(52, 211, 153, 0.45)',
        icon: '🛬',
        desc: '已接地滑跑减速，开启反推脱离跑道',
        tagClass: 'status-tag-landed',
    },
    ROLLOUT: {
        code: 'ROLLOUT',
        label: '跑道滑跑',
        fullLabel: '🛬 跑道滑跑 (ROLLOUT)',
        color: '#34d399',
        hexColor: 0x34d399,
        bgRgba: 'rgba(52, 211, 153, 0.2)',
        borderRgba: 'rgba(52, 211, 153, 0.7)',
        glowRgba: 'rgba(52, 211, 153, 0.45)',
        icon: '🛬',
        desc: '跑道滑跑减速脱离中',
        tagClass: 'status-tag-landed',
    },
    TAXI_IN: {
        code: 'TAXI_IN',
        label: '入位滑行',
        fullLabel: '🚖 入位滑行 (TAXI IN)',
        color: '#c084fc',       // 亮霓虹紫
        hexColor: 0xc084fc,
        bgRgba: 'rgba(192, 132, 252, 0.2)',
        borderRgba: 'rgba(192, 132, 252, 0.7)',
        glowRgba: 'rgba(192, 132, 252, 0.45)',
        icon: '🚖',
        desc: '沿联络滑行道滑向指派机位',
        tagClass: 'status-tag-taxi',
    },
    TAXI_OUT: {
        code: 'TAXI_OUT',
        label: '离位滑行',
        fullLabel: '🚖 离位滑行 (TAXI OUT)',
        color: '#ec4899',       // 玫红紫
        hexColor: 0xec4899,
        bgRgba: 'rgba(236, 72, 153, 0.2)',
        borderRgba: 'rgba(236, 72, 153, 0.7)',
        glowRgba: 'rgba(236, 72, 153, 0.45)',
        icon: '🚖',
        desc: '推出滑行至跑道外等待点',
        tagClass: 'status-tag-taxi',
    },
    TAKEOFF: {
        code: 'TAKEOFF',
        label: '起飞滑跑',
        fullLabel: '🛫 起飞滑跑 (TAKEOFF)',
        color: '#f97316',       // 亮橙色
        hexColor: 0xf97316,
        bgRgba: 'rgba(249, 115, 22, 0.2)',
        borderRgba: 'rgba(249, 115, 22, 0.7)',
        glowRgba: 'rgba(249, 115, 22, 0.45)',
        icon: '🛫',
        desc: '跑道全推力起飞加速中',
        tagClass: 'status-tag-climb',
    },
    PARKED: {
        code: 'PARKED',
        label: '机位停泊',
        fullLabel: '🅿️ 机位停泊 (PARKED)',
        color: '#94a3b8',       // 沉稳蓝灰
        hexColor: 0x94a3b8,
        bgRgba: 'rgba(100, 116, 139, 0.2)',
        borderRgba: 'rgba(100, 116, 139, 0.7)',
        glowRgba: 'rgba(100, 116, 139, 0.35)',
        icon: '🅿️',
        desc: '已停泊于指定机位，登机廊桥对接完成',
        tagClass: 'status-tag-parked',
    },
};

function getFlightStatusConfig(status) {
    return FLIGHT_STATUS_CONFIG[status] || FLIGHT_STATUS_CONFIG.CRUISE;
}

class LiveFlightsModule {
    constructor() {
        this.ctx = null;
        this.THREE = null;
        this.scene = null;
        this.camera = null;
        this.controls = null;

        this.liveGroup = null;
        this.planesMap = new Map(); // id -> { plane, pitchGroup, trailLine, trailGeo, data, targetPos, targetYaw, targetPitch, tagEl, pickProxy }
        
        this.ws = null;
        this.wsUrl = 'ws://127.0.0.1:8765/ws/live';
        this.httpUrl = 'http://127.0.0.1:8765';
        
        this.isConnected = false;
        this.currentMode = 'hkg_live_mapped';
        this.snapToScene = true;
        this.isLiveMode = true; // true = 实时数据, false = 原生演示车流模式
        this.statusFilter = 'all'; // all, ARR, DEP, GND
        this.selectedFlightId = null;
        this.trackedFlightId = null;
        this.followingFlightId = null; // 持续跟随特定航班
        
        this.hudCollapsed = false;
        this.flightListOpen = false;

        this.lastSnapshot = null;
        this.localSimActive = false;
        this.localSimTimer = null;
        
        this.tempVec = null;
        this.tempProj = null;

        // 旋转中心管理与瞄准动效
        this.reticleGroup = null;
        this.reticleMat = null;
        this.selectionAura = null; // 3D 选中飞机光环
        this._centerTween = null;
        this._toastTimer = null;
        this.currentCenterName = '全场中央';
    }

    async init() {
        // 等待宿主场景完成初始化
        const checkScene = () => {
            if (window.__AIRPORT_SCENE__ && window.__AIRPORT_SCENE__.scene && window.__AIRPORT_SCENE__.camera) {
                this.ctx = window.__AIRPORT_SCENE__;
                this.THREE = this.ctx.THREE;
                this.scene = this.ctx.scene;
                this.camera = this.ctx.camera;
                this.controls = this.ctx.controls;
                this.setup();
            } else {
                setTimeout(checkScene, 100);
            }
        };
        checkScene();
    }

    setup() {
        const THREE = this.THREE;
        window.__LIVE_MODULE__ = this;
        this.tempVec = new THREE.Vector3();
        this.tempProj = new THREE.Vector3();

        // 优化相机控制器参数（大幅提升全场景漫游与平移手感）
        this.setupControlsTuning();

        // 创建实时航班专属图层
        this.liveGroup = new THREE.Group();
        this.liveGroup.name = 'LiveFlightsGroup';
        this.scene.add(this.liveGroup);

        // 创建 3D 全息旋转中心指示瞄准环
        this.setupPivotReticle();

        // 创建 3D 航班选中全息光环与扫描柱
        this.setupSelectionAura();

        // 绑定全景双击/中键/单击任意点设为旋转中心交互
        this.setupCanvasInteractions();

        // 默认隐藏原生硬编码的演示飞机
        this.setDemoPlanesVisible(false);

        // 创建 UI 容器
        this.createHUD();
        this.createTagsContainer();

        // 注册到渲染主循环
        if (this.ctx.onFrameCallbacks) {
            this.ctx.onFrameCallbacks.push((now) => this.onFrame(now));
        }

        // 建立 WebSocket 连接
        this.connectWebSocket();
    }

    setDemoPlanesVisible(visible) {
        if (this.ctx.taxiPlanes) {
            this.ctx.taxiPlanes.forEach(p => { if (p.mesh) p.mesh.visible = visible; });
        }
        if (this.ctx.runwayOperationsPlanes) {
            this.ctx.runwayOperationsPlanes.forEach(p => { if (p.mesh) p.mesh.visible = visible; });
        }
    }

    // ==========================================
    // 1. WebSocket 实时通讯与容灾降级
    // ==========================================
    connectWebSocket() {
        if (this.ws) {
            try { this.ws.close(); } catch (e) {}
        }

        try {
            this.ws = new WebSocket(this.wsUrl);
            this.ws.onopen = () => {
                this.isConnected = true;
                this.stopLocalSimulation();
                this.updateHudStatus('🟢 实时流已连接', 'emerald');
                console.log('[LiveFlights] WebSocket 连接成功:', this.wsUrl);
            };

            this.ws.onmessage = (event) => {
                try {
                    const snap = JSON.parse(event.data);
                    this.onSnapshot(snap);
                } catch (e) {
                    console.error('[LiveFlights] 解析数据帧失败:', e);
                }
            };

            this.ws.onclose = () => {
                this.isConnected = false;
                this.updateHudStatus('🟡 离线模式 (高保真推演中)', 'amber');
                this.startLocalSimulation();
                setTimeout(() => this.connectWebSocket(), 3500);
            };

            this.ws.onerror = () => {
                this.isConnected = false;
                this.updateHudStatus('🟡 离线模式 (高保真推演中)', 'amber');
                this.startLocalSimulation();
            };
        } catch (e) {
            this.startLocalSimulation();
        }
    }

    // ==========================================
    // 2. 本地高保真飞行仿真降级（确保离线演示绝无空屏）
    // ==========================================
    startLocalSimulation() {
        if (this.localSimActive) return;
        this.localSimActive = true;
        let simT = 0;

        const profiles = [
            { id: 'wuh_01', hex: '7814b1', flight_no: 'CZ3140', callsign: 'CSN3140', airline: 'CZ', ac_type: 'B789', ac_scale: 1.05, origin: 'PEK', destination: 'WUH', route: 'PEK → WUH', direction: '进港', kind: 'app_04l', speed: 0.002, offset: 0.0, stand: '19' },
            { id: 'wuh_02', hex: '7809a2', flight_no: 'MU2451', callsign: 'CES2451', airline: 'MU', ac_type: 'A333', ac_scale: 1.02, origin: 'PVG', destination: 'WUH', route: 'PVG → WUH', direction: '进港', kind: 'roll_04l', speed: 0.0018, offset: 0.38, stand: '24' },
            { id: 'wuh_03', hex: '7801f4', flight_no: 'CA8215', callsign: 'CCA8215', airline: 'CA', ac_type: 'A321', ac_scale: 0.88, origin: 'WUH', destination: 'CAN', route: 'WUH → CAN', direction: '出港', kind: 'dep_04r', speed: 0.0022, offset: 0.15, stand: '08' },
            { id: 'wuh_04', hex: '7819c9', flight_no: '3U8901', callsign: 'CSC8901', airline: '3U', ac_type: 'A320', ac_scale: 0.86, origin: 'WUH', destination: 'CTU', route: 'WUH → CTU', direction: '出港', kind: 'climb_04r', speed: 0.0025, offset: 0.6, stand: '33' },
            { id: 'wuh_05', hex: '7822d3', flight_no: '9C6381', callsign: 'CQH6381', airline: '9C', ac_type: 'A320', ac_scale: 0.85, origin: 'HAK', destination: 'WUH', route: 'HAK → WUH', direction: '进港', kind: 'taxi', speed: 0.0015, offset: 0.28, stand: '12' },
            { id: 'wuh_06', hex: '7805d8', flight_no: 'MF8401', callsign: 'CXA8401', airline: 'MF', ac_type: 'B738', ac_scale: 0.87, origin: 'XMN', destination: 'WUH', route: 'XMN → WUH', direction: '场面', kind: 'parked', speed: 0.0, offset: 0.0, stand: '21' },
            { id: 'wuh_07', hex: '7811ef', flight_no: 'ZH9873', callsign: 'CSZ9873', airline: 'ZH', ac_type: 'B738', ac_scale: 0.86, origin: 'SZX', destination: 'WUH', route: 'SZX → WUH', direction: '场面', kind: 'parked', speed: 0.0, offset: 0.0, stand: '42' },
            { id: 'wuh_08', hex: '7800cc', flight_no: 'CZ659', callsign: 'CSN659', airline: 'CZ', ac_type: 'B77W', ac_scale: 1.10, origin: 'WUH', destination: 'SFO', route: 'WUH → SFO', direction: '场面', kind: 'parked', speed: 0.0, offset: 0.0, stand: '30' },
            { id: 'wuh_09', hex: '7818aa', flight_no: 'HO1132', callsign: 'DKH1132', airline: 'HO', ac_type: 'A320', ac_scale: 0.85, origin: 'SHA', destination: 'WUH', route: 'SHA → WUH', direction: '场面', kind: 'parked', speed: 0.0, offset: 0.0, stand: '205' },
            { id: 'wuh_10', hex: '7890bb', flight_no: 'CX924', callsign: 'CPA924', airline: 'CX', ac_type: 'A359', ac_scale: 1.05, origin: 'HKG', destination: 'WUH', route: 'HKG → WUH', direction: '进港', kind: 'app_dist', speed: 0.0019, offset: 0.72, stand: '18' },
        ];

        this.localSimTimer = setInterval(() => {
            simT += 1.0;
            const flights = [];
            const occupied = [];

            profiles.forEach(p => {
                const t = (simT * p.speed + p.offset) % 1.0;
                let x = 0, y = 0.3, z = 0, yaw = 0, pitch = 0, status = 'APPROACH', gs = 140, alt_baro = 1200;

                if (p.kind === 'app_04l') {
                    z = 2400 - t * 2000;
                    x = -365;
                    if (z > 465) {
                        alt_baro = Math.round((z - 465) * 1.5 + 100);
                        y = 15.0 + (alt_baro / 3500.0) * 140.0;
                        status = 'APPROACH';
                        pitch = -0.05;
                        gs = 145 - t * 20;
                    } else {
                        alt_baro = 'ground';
                        y = 0.3;
                        status = 'LANDED';
                        pitch = 0;
                        gs = Math.max(25, 110 - (465 - z) * 0.15);
                    }
                    yaw = 0;
                } else if (p.kind === 'roll_04l') {
                    z = 465 - t * 900;
                    x = -365;
                    y = 0.3;
                    alt_baro = 'ground';
                    status = 'LANDED';
                    yaw = 0;
                    pitch = 0;
                    gs = Math.max(20, 130 * (1 - t * 0.8));
                } else if (p.kind === 'dep_04r') {
                    z = 480 - t * 2280;
                    x = 375;
                    if (z > 80) {
                        y = 0.3;
                        alt_baro = 'ground';
                        status = 'TAKEOFF';
                        pitch = 0;
                        gs = 50 + (480 - z) * 0.3;
                    } else {
                        alt_baro = Math.round((80 - z) * 1.35);
                        y = 15.0 + (alt_baro / 3000.0) * 170.0;
                        status = 'CLIMB';
                        pitch = 0.16;
                        gs = 175 + t * 40;
                    }
                    yaw = 0;
                } else if (p.kind === 'climb_04r') {
                    z = -400 - t * 2100;
                    x = 375 + Math.sin(t * Math.PI) * 120;
                    alt_baro = Math.round(1500 + (400 - z) * 1.4);
                    y = 180.0 + (alt_baro / 5000.0) * 120.0;
                    status = 'CLIMB';
                    yaw = -0.03;
                    pitch = 0.18;
                    gs = 240;
                } else if (p.kind === 'app_dist') {
                    z = 2800 - t * 1500;
                    x = -365 - Math.sin(t * Math.PI) * 70;
                    alt_baro = Math.round(1800 + (z - 1300) * 0.8);
                    y = 140.0 + (z - 1300) * 0.05;
                    status = 'APPROACH';
                    yaw = 0;
                    pitch = -0.06;
                    gs = 180;
                } else if (p.kind === 'taxi') {
                    x = -302 + Math.sin(t * Math.PI) * 160;
                    z = -120 + t * 160;
                    y = 0.22;
                    alt_baro = 'ground'; status = 'TAXI_IN'; yaw = -Math.PI / 2; pitch = 0; gs = 18;
                } else {
                    // Parked
                    occupied.push(p.stand);
                    if (p.stand === '21') { x = -140; z = 43; }
                    else if (p.stand === '42') { x = 140; z = 43; }
                    else if (p.stand === '30') { x = 0; z = 52; }
                    else if (p.stand === '205') { x = -65; z = 310; }
                    y = 0.22;
                    alt_baro = 'ground'; status = 'PARKED'; yaw = 0; pitch = 0; gs = 0;
                }

                const info = AIRLINE_INFO[p.airline] || AIRLINE_INFO.default;
                flights.push({
                    id: p.id,
                    hex: p.hex,
                    flight_no: p.flight_no,
                    callsign: p.callsign,
                    airline: p.airline,
                    airline_name: info.name,
                    tail_color: info.tail,
                    ac_type: p.ac_type,
                    ac_scale: p.ac_scale,
                    alt_baro: alt_baro,
                    gs: gs,
                    track: 0,
                    baro_rate: pitch > 0 ? 1500 : (pitch < 0 ? -700 : 0),
                    dist_km: (Math.hypot(x, z) * 2 / 1000).toFixed(1),
                    direction: p.direction,
                    origin: p.origin,
                    destination: p.destination,
                    route: p.route,
                    status: status,
                    stand: p.stand,
                    gate_name: `${p.stand} 号机位`,
                    scene_x: Number(x.toFixed(2)),
                    scene_y: Number(y.toFixed(2)),
                    scene_z: Number(z.toFixed(2)),
                    scene_yaw: Number(yaw.toFixed(4)),
                    scene_pitch: Number(pitch.toFixed(4)),
                    trail: [[x, y, z], [x, y, z + 60], [x, y, z + 120]],
                    updated_at: Date.now() / 1000
                });
            });

            this.onSnapshot({
                timestamp: Date.now() / 1000,
                mode: 'wuh_sim_fallback',
                source: 'client_high_fidelity_sim',
                snap_to_scene: true,
                total_flights: flights.length,
                airborne_count: flights.filter(f => !['PARKED', 'TAXI_IN', 'LANDED'].includes(f.status)).length,
                ground_count: flights.filter(f => ['PARKED', 'TAXI_IN', 'LANDED'].includes(f.status)).length,
                flights: flights,
                stands_occupied: occupied
            });
        }, 1000);
    }

    stopLocalSimulation() {
        this.localSimActive = false;
        if (this.localSimTimer) {
            clearInterval(this.localSimTimer);
            this.localSimTimer = null;
        }
    }

    // ==========================================
    // 3. 实时快照处理、模型实例化与机位联动
    // ==========================================
    onSnapshot(snap) {
        this.lastSnapshot = snap;
        this.updateHudMetrics(snap);

        if (!this.isLiveMode) return;

        const currentIds = new Set();
        const incomingOccupiedStands = new Set(snap.stands_occupied || []);

        // 1. 同步在场航班飞机
        snap.flights.forEach(f => {
            // 依据过滤条件决定是否渲染
            if (this.statusFilter === 'ARR' && f.direction !== '进港') return;
            if (this.statusFilter === 'DEP' && f.direction !== '出港') return;
            if (this.statusFilter === 'GND' && !['PARKED', 'TAXI_IN', 'TAXI_OUT', 'LANDED'].includes(f.status)) return;

            currentIds.add(f.id);
            this.updateOrCreateFlightMesh(f);
        });

        // 2. 清理离开视界的飞机
        for (const [id, item] of this.planesMap.entries()) {
            if (!currentIds.has(id)) {
                this.liveGroup.remove(item.plane);
                if (item.trailLine) this.liveGroup.remove(item.trailLine);
                if (item.tagEl && item.tagEl.parentNode) item.tagEl.remove();
                this.planesMap.delete(id);
            }
        }

        // 3. 停机位与立体廊桥伸缩联动
        if (this.ctx.standRegistry) {
            this.ctx.standRegistry.forEach(node => {
                const ref = node.data.ref;
                const isOccupiedByLive = incomingOccupiedStands.has(ref);
                if (isOccupiedByLive) {
                    node.currentOccupied = true;
                    if (node.planeMesh) node.planeMesh.visible = true;
                    if (node.gse) node.gse.visible = true;
                    if (node.labelEl) node.labelEl.classList.add('live-docked');
                }
            });
        }

        // 4. 刷新右侧侧边栏航班列表
        this.renderFlightList(snap.flights);

        // 5. 刷新选中航班详情卡片
        if (this.selectedFlightId) {
            const target = snap.flights.find(f => f.id === this.selectedFlightId);
            if (target) this.renderFlightCard(target);
        }
    }

    updateOrCreateFlightMesh(flight) {
        let item = this.planesMap.get(flight.id);
        const THREE = this.THREE;

        if (!item) {
            // 复用宿主原有 createAircraftModel 创建高写实飞机模型
            const tailColor = flight.tail_color || 0x0284c7;
            const isAirborne = flight.status !== 'PARKED' && flight.status !== 'TAXI_IN' && flight.status !== 'TAXI_OUT' && flight.status !== 'LANDED';
            const scaleMultiplier = isAirborne ? 1.6 : 1.0;
            const plane = this.ctx.createAircraftModel(flight.ac_scale || 0.88, tailColor);
            plane.scale.multiplyScalar(scaleMultiplier);
            plane.position.set(flight.scene_x, flight.scene_y, flight.scene_z);
            plane.rotation.y = flight.scene_yaw || 0;
            if (plane.userData.pitchGroup) {
                plane.userData.pitchGroup.rotation.x = flight.scene_pitch || 0;

                // 空中客机添加机翼航行灯（左红右绿），增强空中立体辨识度
                if (isAirborne && plane.userData.dims) {
                    const d = plane.userData.dims;
                    const navGeo = new THREE.SphereGeometry(0.32, 6, 6);
                    const lNav = new THREE.Mesh(navGeo, new THREE.MeshBasicMaterial({ color: 0xef4444 }));
                    lNav.position.set(-d.span * 0.49, d.wingY || 1.5, -d.len * 0.08);
                    const rNav = new THREE.Mesh(navGeo, new THREE.MeshBasicMaterial({ color: 0x22c55e }));
                    rNav.position.set(d.span * 0.49, d.wingY || 1.5, -d.len * 0.08);
                    plane.userData.pitchGroup.add(lNav);
                    plane.userData.pitchGroup.add(rNav);
                }
            }
            this.liveGroup.add(plane);

            // 点击模型选中该飞机
            plane.userData.flightId = flight.id;
            plane.traverse(child => {
                if (child.isMesh) child.userData.flightId = flight.id;
            });

            // 增加大体积不可见拾取代理球，确保任何距离/视角下一键精准命中
            const pickGeo = new THREE.SphereGeometry(26, 8, 8);
            const pickMat = new THREE.MeshBasicMaterial({ visible: false });
            const pickProxy = new THREE.Mesh(pickGeo, pickMat);
            pickProxy.name = 'pickProxy';
            pickProxy.userData.flightId = flight.id;
            plane.add(pickProxy);

            // 创建 3D 动态发光航迹线 (Line) - 按状态颜色标准赋色
            const statusCfg = getFlightStatusConfig(flight.status);
            const trailGeo = new THREE.BufferGeometry();
            const initPts = (flight.trail && flight.trail.length > 0)
                ? flight.trail.map(p => new THREE.Vector3(p[0], p[1], p[2]))
                : [new THREE.Vector3(flight.scene_x, flight.scene_y, flight.scene_z)];
            trailGeo.setFromPoints(initPts);

            const trailMat = new THREE.LineBasicMaterial({
                color: statusCfg.hexColor,
                transparent: true,
                opacity: 0.82,
                linewidth: 2,
            });
            const trailLine = new THREE.Line(trailGeo, trailMat);
            this.liveGroup.add(trailLine);

            // 创建 2D 标牌 DOM 元素 - 统一注入颜色标准样式
            const altText = isAirborne ? `☁️ ${flight.alt_baro}ft` : (flight.stand ? `🅿️ ${flight.stand}机位` : '🛬 地面');
            const tagEl = document.createElement('div');
            tagEl.className = `live-flight-tag ${statusCfg.tagClass} ${isAirborne ? 'tag-air' : 'tag-gnd'}`;
            tagEl.innerHTML = `
                <div class="tag-header">
                    <span class="tag-badge" style="background:${(AIRLINE_INFO[flight.airline]||AIRLINE_INFO.default).color}">${flight.airline}</span>
                    <span class="tag-no">${flight.flight_no}</span>
                    <span class="tag-status-pill" style="background:${statusCfg.bgRgba}; border-color:${statusCfg.borderRgba}; color:${statusCfg.color};">${statusCfg.icon} ${statusCfg.label}</span>
                </div>
                <div class="tag-body">
                    <div class="tag-route">${flight.route}</div>
                    <div class="tag-meta"><span>${flight.ac_type}</span> <span class="tag-alt-pill" style="color:${statusCfg.color}; font-weight:700;">${altText}</span></div>
                </div>
            `;
            tagEl.onclick = (e) => {
                e.stopPropagation();
                this.selectFlight(flight.id);
            };
            document.getElementById('live-flight-tags').appendChild(tagEl);

            item = {
                plane,
                pitchGroup: plane.userData.pitchGroup,
                trailLine,
                trailGeo,
                data: flight,
                targetPos: new THREE.Vector3(flight.scene_x, flight.scene_y, flight.scene_z),
                targetYaw: flight.scene_yaw || 0,
                targetPitch: flight.scene_pitch || 0,
                tagEl
            };
            this.planesMap.set(flight.id, item);
        } else {
            // 更新目标位姿与航迹
            const statusCfg = getFlightStatusConfig(flight.status);
            item.data = flight;
            item.targetPos.set(flight.scene_x, flight.scene_y, flight.scene_z);
            item.targetYaw = flight.scene_yaw || 0;
            item.targetPitch = flight.scene_pitch || 0;

            const isAir = flight.status !== 'PARKED' && flight.status !== 'TAXI_IN' && flight.status !== 'TAXI_OUT' && flight.status !== 'LANDED';
            const scaleMultiplier = isAir ? 1.6 : 1.0;
            if (item.plane && item.plane.userData.dims) {
                item.plane.scale.setScalar(item.plane.userData.dims.sf * scaleMultiplier);
            }

            // 更新航迹点集与动态状态颜色
            if (item.trailLine && item.trailLine.material) {
                item.trailLine.material.color.setHex(statusCfg.hexColor);
            }
            if (flight.trail && flight.trail.length > 1) {
                const pts = flight.trail.map(p => new THREE.Vector3(p[0], p[1], p[2]));
                if (item.trailLine.geometry) item.trailLine.geometry.dispose();
                item.trailLine.geometry = new THREE.BufferGeometry().setFromPoints(pts);
            }

            // 更新标牌文本内容与状态徽章
            if (item.tagEl) {
                const isAir = flight.status !== 'PARKED' && flight.status !== 'TAXI_IN' && flight.status !== 'TAXI_OUT' && flight.status !== 'LANDED';
                const altT = isAir ? `☁️ ${flight.alt_baro}ft` : (flight.stand ? `🅿️ ${flight.stand}机位` : '🛬 地面');
                item.tagEl.className = `live-flight-tag ${statusCfg.tagClass} ${isAir ? 'tag-air' : 'tag-gnd'}${this.selectedFlightId === flight.id ? ' selected' : ''}`;
                const routeEl = item.tagEl.querySelector('.tag-route');
                const metaEl = item.tagEl.querySelector('.tag-meta');
                const statusPill = item.tagEl.querySelector('.tag-status-pill');
                if (routeEl) routeEl.textContent = flight.route;
                if (metaEl) metaEl.innerHTML = `<span>${flight.ac_type}</span> <span class="tag-alt-pill" style="color:${statusCfg.color}; font-weight:700;">${altT}</span>`;
                if (statusPill) {
                    statusPill.style.background = statusCfg.bgRgba;
                    statusPill.style.borderColor = statusCfg.borderRgba;
                    statusPill.style.color = statusCfg.color;
                    statusPill.innerHTML = `${statusCfg.icon} ${statusCfg.label}`;
                }
            }
        }
    }

    // ==========================================
    // 4. 逐帧渲染循环插值与标牌屏幕投影
    // ==========================================
    onFrame(now) {
        if (!this.isLiveMode) return;
        const camera = this.camera;
        const width = window.innerWidth;
        const height = window.innerHeight;

        this.planesMap.forEach((item, id) => {
            const p = item.plane;
            // 1. 位置弹性平滑插值 (Lerp)
            p.position.lerp(item.targetPos, 0.08);

            // 2. 偏航角平滑过渡 (处理 [-PI, PI] 越界)
            let diffYaw = item.targetYaw - p.rotation.y;
            while (diffYaw < -Math.PI) diffYaw += Math.PI * 2;
            while (diffYaw > Math.PI) diffYaw -= Math.PI * 2;
            p.rotation.y += diffYaw * 0.1;

            // 3. 俯仰角平滑过渡
            if (item.pitchGroup) {
                item.pitchGroup.rotation.x += (item.targetPitch - item.pitchGroup.rotation.x) * 0.1;
            }

            // 4. 标牌 3D -> 2D 屏幕投影
            if (item.tagEl) {
                this.tempVec.set(p.position.x, p.position.y + 4.5, p.position.z);
                this.tempVec.project(camera);

                // 判断是否在相机视野前方且在屏幕可见安全区域内
                const inFront = this.tempVec.z < 1.0;
                const distToCam = camera.position.distanceTo(p.position);
                const isVisible = inFront && distToCam < 3200 &&
                                  this.tempVec.x >= -1.1 && this.tempVec.x <= 1.1 &&
                                  this.tempVec.y >= -1.1 && this.tempVec.y <= 1.1;

                if (isVisible) {
                    const sx = (this.tempVec.x * 0.5 + 0.5) * width;
                    const sy = (-this.tempVec.y * 0.5 + 0.5) * height;
                    item.tagEl.style.transform = `translate3d(${Math.round(sx)}px, ${Math.round(sy)}px, 0)`;
                    item.tagEl.style.display = 'block';
                    if (this.selectedFlightId === id) {
                        item.tagEl.classList.add('selected');
                    } else {
                        item.tagEl.classList.remove('selected');
                    }
                } else {
                    item.tagEl.style.display = 'none';
                }
            }

            // 5. 视角完全自由漫游，不再强制逐帧锁定中心点
        });

        // 6. 若用户开启持续跟随特定航班，相机平移跟随（旋转手柄保持全自由）
        if (this.followingFlightId && this.controls) {
            const followItem = this.planesMap.get(this.followingFlightId);
            if (followItem && followItem.plane) {
                const curPos = followItem.plane.position;
                const delta = curPos.clone().sub(this.controls.target);
                this.controls.target.copy(curPos);
                this.camera.position.add(delta);
            }
        }

        // 7. 更新 3D 选中飞机全息光环位置与动态旋转及对应状态色彩
        if (this.selectedFlightId && this.selectionAura) {
            const selItem = this.planesMap.get(this.selectedFlightId);
            if (selItem && selItem.plane) {
                this.selectionAura.visible = true;
                this.selectionAura.position.copy(selItem.plane.position);
                this.selectionAura.rotation.y = (now * 0.0015) % (Math.PI * 2);
                const stCfg = getFlightStatusConfig(selItem.data.status);
                this.selectionAura.children.forEach(mesh => {
                    if (mesh.material && mesh.material.color) mesh.material.color.setHex(stCfg.hexColor);
                });
            } else {
                this.selectionAura.visible = false;
            }
        } else if (this.selectionAura) {
            this.selectionAura.visible = false;
        }
    }

    // ==========================================
    // 5. 飞常准风格雷达 HUD 与控制面板构建
    // ==========================================
    createHUD() {
        // 创建独立顶层容器，严格规避原纯模型 CSS 隐藏规则
        const hud = document.createElement('div');
        hud.id = 'live-radar-hud';
        hud.className = 'live-radar-hud';
        hud.innerHTML = `
            <!-- 顶部标题与折叠开关 -->
            <div class="hud-header">
                <div class="hud-brand">
                    <span class="radar-dot"></span>
                    <span class="hud-title">天河机场 · 3D实时航班孪生</span>
                    <span id="hud-status-badge" class="hud-status-badge emerald">🟢 实时流</span>
                </div>
                <div class="hud-actions">
                    <button id="btn-toggle-flightlist" class="hud-btn-sm" title="展开/收起航班流清单">📋 航班表</button>
                    <button id="btn-toggle-hud" class="hud-btn-sm" title="折叠/展开面板">📐 折叠</button>
                </div>
            </div>

            <!-- 数据概览栏 -->
            <div class="hud-metrics" id="hud-metrics">
                <div class="metric-card">
                    <div class="metric-val" id="m-total">0</div>
                    <div class="metric-lbl">在场实时</div>
                </div>
                <div class="metric-card">
                    <div class="metric-val" id="m-air">0</div>
                    <div class="metric-lbl">空中进离</div>
                </div>
                <div class="metric-card">
                    <div class="metric-val" id="m-gnd">0</div>
                    <div class="metric-lbl">机位靠桥</div>
                </div>
                <div class="metric-card">
                    <div class="metric-val" id="m-src">HKG</div>
                    <div class="metric-lbl">数据源模式</div>
                </div>
            </div>

            <!-- 控制工具条 -->
            <div class="hud-controls" id="hud-controls">
                <!-- 模式切换与吸附 -->
                <div class="control-row">
                    <div class="btn-group">
                        <button id="btn-mode-hkg" class="mode-btn active">香港真流 (映射)</button>
                        <button id="btn-mode-wuh" class="mode-btn">武汉天河 (原生)</button>
                    </div>
                    <button id="btn-snap" class="toggle-pill active" title="智能吸附至 3D 跑道中心线与 75 个机位">跑道吸附: 开</button>
                </div>

                <!-- 🎨 飞行状态颜色标准图例 (Color Standards Legend) -->
                <div class="control-row status-legend-row">
                    <div class="legend-header">
                        <span class="text-xs font-bold text-slate-300">🎨 飞行状态颜色标准:</span>
                        <span class="text-2xs text-slate-400">3D航迹/标牌/状态全景对齐</span>
                    </div>
                    <div class="legend-grid">
                        <span class="legend-chip approach" title="五边盲降进近下滑中 (3°下滑线)"><span class="dot"></span>🔵 进近 (04L)</span>
                        <span class="legend-chip climb" title="起飞离港爬升中 (标准SID离场)"><span class="dot"></span>🟠 爬升 (04R)</span>
                        <span class="legend-chip cruise" title="巡航高度层平飞"><span class="dot"></span>🟢 巡航平飞</span>
                        <span class="legend-chip taxi" title="场面联络道滑行"><span class="dot"></span>🟣 地面滑行</span>
                        <span class="legend-chip parked" title="机位停泊与廊桥对接"><span class="dot"></span>🅿️ 机位靠桥</span>
                    </div>
                </div>

                <!-- 航班分类过滤 -->
                <div class="control-row">
                    <div class="filter-group">
                        <button class="filter-btn active" data-filter="all">全部</button>
                        <button class="filter-btn" data-filter="ARR">进港</button>
                        <button class="filter-btn" data-filter="DEP">出港</button>
                        <button class="filter-btn" data-filter="GND">地面/机位</button>
                    </div>
                </div>

                <!-- 视点快速转场 -->
                <div class="control-row views-row">
                    <button class="cam-btn" data-view="overview">🌐 全场景</button>
                    <button class="cam-btn" data-view="app04l">🛬 04L五边</button>
                    <button class="cam-btn" data-view="dep04r">🛫 04R离港</button>
                    <button class="cam-btn" data-view="t3apron">🏢 T3机坪</button>
                    <button class="cam-btn" data-view="t2apron">🅿️ T2机坪</button>
                </div>

                <!-- 旋转中心自由选择 -->
                <div class="control-row center-row">
                    <div class="center-header">
                        <span class="text-xs font-bold text-sky-300">🎯 旋转中心:</span>
                        <span id="hud-center-name" class="hud-center-pill">全场中央</span>
                    </div>
                    <div class="center-btn-group">
                        <button class="center-btn active" data-center="center" title="重设中心为机场中心">全场</button>
                        <button class="center-btn" data-center="rwy04l" title="重设中心为西跑道04L">04L跑道</button>
                        <button class="center-btn" data-center="rwy04r" title="重设中心为东跑道04R">04R跑道</button>
                        <button class="center-btn" data-center="t3" title="重设中心为T3主航站楼">T3主楼</button>
                        <button class="center-btn" data-center="t2" title="重设中心为T2航站区">T2航站</button>
                    </div>
                </div>
                <div class="hud-hint">💡 双击场景任意地面/飞机即可设为中心；右键平移/滚轮缩放</div>

                <!-- 原生演示模式与录像回放 -->
                <div class="control-row bottom-row">
                    <button id="btn-toggle-demo" class="hud-btn-alt">切回原演示飞机</button>
                    <div class="replay-bar">
                        <button id="btn-replay-play" class="replay-btn">▶</button>
                        <button id="btn-replay-live" class="replay-btn active" title="切回最新实时数据">LIVE</button>
                        <span class="replay-time" id="replay-time-lbl">00:00:00</span>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(hud);

        // 创建侧边栏航班清单
        const drawer = document.createElement('div');
        drawer.id = 'live-flight-drawer';
        drawer.className = 'live-flight-drawer hidden';
        drawer.innerHTML = `
            <div class="drawer-header">
                <span class="font-bold text-sm text-sky-200">📡 在场航班动态实时队列</span>
                <button id="btn-close-drawer" class="text-slate-400 hover:text-white">✕</button>
            </div>
            <div class="drawer-list" id="drawer-flight-list"></div>
        `;
        document.body.appendChild(drawer);

        // 创建航班遥测详情弹卡
        const card = document.createElement('div');
        card.id = 'live-flight-card';
        card.className = 'live-flight-card hidden';
        document.body.appendChild(card);

        // 绑定事件处理器
        this.bindHudEvents();
        this.injectHudStyles();
    }

    createTagsContainer() {
        const tags = document.createElement('div');
        tags.id = 'live-flight-tags';
        tags.className = 'live-flight-tags';
        document.body.appendChild(tags);
    }

    bindHudEvents() {
        // 模式切换
        document.getElementById('btn-mode-hkg').onclick = () => this.switchFeedMode('hkg_live_mapped');
        document.getElementById('btn-mode-wuh').onclick = () => this.switchFeedMode('wuh_native');

        // 吸附开关
        const snapBtn = document.getElementById('btn-snap');
        snapBtn.onclick = () => {
            this.snapToScene = !this.snapToScene;
            snapBtn.textContent = `跑道吸附: ${this.snapToScene ? '开' : '关'}`;
            snapBtn.classList.toggle('active', this.snapToScene);
            this.sendModeConfig();
        };

        // 状态过滤
        document.querySelectorAll('.filter-btn').forEach(btn => {
            btn.onclick = () => {
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.statusFilter = btn.dataset.filter;
            };
        });

        // 视点切换
        document.querySelectorAll('.cam-btn').forEach(btn => {
            btn.onclick = () => this.switchCameraPreset(btn.dataset.view);
        });

        // 旋转中心快捷切换
        document.querySelectorAll('.center-btn').forEach(btn => {
            btn.onclick = () => {
                document.querySelectorAll('.center-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const targetKey = btn.dataset.center;
                if (targetKey === 'center') {
                    this.resetCenter();
                } else if (targetKey === 'rwy04l') {
                    this.setOrbitCenter(new this.THREE.Vector3(-365, 5, 0), { label: '西跑道 04L/22R', targetDistance: 450 });
                } else if (targetKey === 'rwy04r') {
                    this.setOrbitCenter(new this.THREE.Vector3(375, 5, 0), { label: '东跑道 04R/22L', targetDistance: 450 });
                } else if (targetKey === 't3') {
                    this.setOrbitCenter(new this.THREE.Vector3(0, 15, 42), { label: 'T3 主航站楼', targetDistance: 320 });
                } else if (targetKey === 't2') {
                    this.setOrbitCenter(new this.THREE.Vector3(-65, 5, 310), { label: 'T2 航站区', targetDistance: 280 });
                }
            };
        });

        // 切回演示飞机
        const demoBtn = document.getElementById('btn-toggle-demo');
        demoBtn.onclick = () => {
            this.isLiveMode = !this.isLiveMode;
            demoBtn.textContent = this.isLiveMode ? '切回原演示飞机' : '激活实时真流';
            this.setDemoPlanesVisible(!this.isLiveMode);
            this.liveGroup.visible = this.isLiveMode;
            document.getElementById('live-flight-tags').style.display = this.isLiveMode ? 'block' : 'none';
        };

        // 折叠面板
        const hudToggle = document.getElementById('btn-toggle-hud');
        hudToggle.onclick = () => {
            this.hudCollapsed = !this.hudCollapsed;
            document.getElementById('hud-metrics').classList.toggle('hidden', this.hudCollapsed);
            document.getElementById('hud-controls').classList.toggle('hidden', this.hudCollapsed);
            hudToggle.textContent = this.hudCollapsed ? '展开' : '折叠';
        };

        // 航班表侧边栏开关
        document.getElementById('btn-toggle-flightlist').onclick = () => {
            const drawer = document.getElementById('live-flight-drawer');
            drawer.classList.toggle('hidden');
        };
        document.getElementById('btn-close-drawer').onclick = () => {
            document.getElementById('live-flight-drawer').classList.add('hidden');
        };

        // 回放控制
        document.getElementById('btn-replay-play').onclick = () => this.controlReplay('play');
        document.getElementById('btn-replay-live').onclick = () => this.controlReplay('live');
    }

    switchFeedMode(mode) {
        this.currentMode = mode;
        document.getElementById('btn-mode-hkg').classList.toggle('active', mode === 'hkg_live_mapped');
        document.getElementById('btn-mode-wuh').classList.toggle('active', mode === 'wuh_native');
        document.getElementById('m-src').textContent = mode === 'hkg_live_mapped' ? 'HKG' : 'WUH';

        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ action: 'switch_mode', mode: mode, snap_to_scene: this.snapToScene }));
        } else {
            fetch(`${this.httpUrl}/api/flights/mode`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode: mode, snap_to_scene: this.snapToScene })
            }).catch(() => {});
        }
    }

    sendModeConfig() {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ action: 'switch_mode', mode: this.currentMode, snap_to_scene: this.snapToScene }));
        }
    }

    controlReplay(action) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ action: action }));
        }
        document.getElementById('btn-replay-live').classList.toggle('active', action === 'live');
    }

    switchCameraPreset(preset) {
        if (!this.controls || !this.camera) return;
        const P = {
            overview: { pos: [880, 480, 560],   tgt: [-60, 50, 20] },
            app04l:   { pos: [-480, 140, 850],  tgt: [-365, 60, 350] },
            dep04r:   { pos: [460, 120, 180],   tgt: [375, 90, -420] },
            t3apron:  { pos: [0, 160, 260],     tgt: [0, 8, 42] },
            t2apron:  { pos: [-65, 130, 440],   tgt: [-65, 5, 310] },
        }[preset] || { pos: [880, 480, 560], tgt: [-60, 50, 20] };

        // 启动平滑运镜
        let startTime = performance.now();
        const duration = 1200;
        const startPos = this.camera.position.clone();
        const startTgt = this.controls.target.clone();
        const endPos = new this.THREE.Vector3(...P.pos);
        const endTgt = new this.THREE.Vector3(...P.tgt);

        const tickTween = () => {
            const elapsed = performance.now() - startTime;
            const progress = Math.min(elapsed / duration, 1.0);
            const ease = 1 - Math.pow(1 - progress, 3);

            this.camera.position.lerpVectors(startPos, endPos, ease);
            this.controls.target.lerpVectors(startTgt, endTgt, ease);

            if (progress < 1.0) requestAnimationFrame(tickTween);
        };
        requestAnimationFrame(tickTween);
    }

    updateHudMetrics(snap) {
        document.getElementById('m-total').textContent = snap.total_flights || 0;
        document.getElementById('m-air').textContent = snap.airborne_count || 0;
        document.getElementById('m-gnd').textContent = snap.ground_count || 0;
        document.getElementById('m-src').textContent = snap.mode.includes('hkg') ? 'HKG真流' : 'WUH原生';
    }

    updateHudStatus(text, color) {
        const el = document.getElementById('hud-status-badge');
        if (el) {
            el.textContent = text;
            el.className = `hud-status-badge ${color}`;
        }
    }

    renderFlightList(flights) {
        const container = document.getElementById('drawer-flight-list');
        if (!container || document.getElementById('live-flight-drawer').classList.contains('hidden')) return;

        let html = '';
        flights.forEach(f => {
            const info = AIRLINE_INFO[f.airline] || AIRLINE_INFO.default;
            const stCfg = getFlightStatusConfig(f.status);
            const isSelected = f.id === this.selectedFlightId;
            html += `
                <div class="flight-list-item ${isSelected ? 'selected' : ''}" onclick="window.__LIVE_MODULE__.selectFlight('${f.id}')">
                    <div class="flex items-center justify-between">
                        <span class="font-bold text-white">${f.flight_no}</span>
                        <span class="text-xs px-1.5 py-0.5 rounded text-white" style="background:${info.color}">${f.airline}</span>
                    </div>
                    <div class="flex items-center justify-between text-xs text-slate-300 mt-1">
                        <span class="font-mono">${f.route}</span>
                        <span class="text-2xs px-1.5 py-0.5 rounded font-bold" style="background:${stCfg.bgRgba}; border:1px solid ${stCfg.borderRgba}; color:${stCfg.color};">${stCfg.icon} ${stCfg.label}</span>
                    </div>
                    <div class="flex items-center justify-between text-2xs text-slate-400 mt-0.5">
                        <span>${f.ac_type} · ${f.alt_baro === 'ground' ? '地面' : f.alt_baro + 'ft'}</span>
                        <span>${f.stand ? f.stand + '号机位' : '距场 ' + f.dist_km + 'km'}</span>
                    </div>
                </div>
            `;
        });
        container.innerHTML = html;
    }

    // ==========================================
    // 5. 旋转中心自由设定体系（双击/点击/中键任意位置聚焦）
    // ==========================================
    setupControlsTuning() {
        if (!this.controls) return;
        this.controls.enablePan = true;
        this.controls.screenSpacePanning = true;
        this.controls.panSpeed = 1.35;         // 提升右键平移拖拽灵敏度
        this.controls.rotateSpeed = 0.85;      // 适中旋转速度
        this.controls.zoomSpeed = 1.25;        // 灵敏滚轮缩放
        this.controls.dampingFactor = 0.08;    // 阻尼弹性停顿
        this.controls.minDistance = 8;
        this.controls.maxDistance = 4500;
    }

    setupPivotReticle() {
        const THREE = this.THREE;
        this.reticleGroup = new THREE.Group();
        this.reticleGroup.name = 'LivePivotReticle';
        this.reticleGroup.visible = false;

        this.reticleMat = new THREE.MeshBasicMaterial({
            color: 0x38bdf8,
            transparent: true,
            opacity: 0.9,
            depthTest: false,
            side: THREE.DoubleSide
        });

        // 1. 外发光环
        const outerRing = new THREE.Mesh(new THREE.RingGeometry(2.4, 2.9, 36), this.reticleMat);
        outerRing.rotation.x = -Math.PI / 2;
        this.reticleGroup.add(outerRing);

        // 2. 内瞄准环
        const innerRing = new THREE.Mesh(new THREE.RingGeometry(0.6, 0.9, 24), this.reticleMat);
        innerRing.rotation.x = -Math.PI / 2;
        this.reticleGroup.add(innerRing);

        // 3. 四向刻度准心
        const ticks = [
            { pos: [0, 0, -3.4], size: [0.35, 0.06, 1.4] },
            { pos: [0, 0, 3.4],  size: [0.35, 0.06, 1.4] },
            { pos: [-3.4, 0, 0], size: [1.4, 0.06, 0.35] },
            { pos: [3.4, 0, 0],  size: [1.4, 0.06, 0.35] }
        ];
        ticks.forEach(t => {
            const mesh = new THREE.Mesh(new THREE.BoxGeometry(...t.size), this.reticleMat);
            mesh.position.set(...t.pos);
            this.reticleGroup.add(mesh);
        });

        this.scene.add(this.reticleGroup);
    }

    showPivotReticle(pos) {
        if (!this.reticleGroup) return;
        this.reticleGroup.position.set(pos.x, Math.max(0.3, pos.y + 0.15), pos.z);
        this.reticleGroup.scale.setScalar(0.25);
        this.reticleGroup.visible = true;
        this.reticleMat.opacity = 0.95;

        const startTime = performance.now();
        const duration = 1200;

        const anim = (now) => {
            const elapsed = now - startTime;
            const progress = Math.min(elapsed / duration, 1.0);
            const ease = 1 - Math.pow(1 - progress, 3);

            const s = 0.25 + ease * 0.75;
            this.reticleGroup.scale.set(s, s, s);
            this.reticleGroup.rotation.y += 0.025;

            if (progress > 0.5) {
                this.reticleMat.opacity = 0.95 * (1.0 - (progress - 0.5) / 0.5);
            }

            if (progress < 1.0) {
                requestAnimationFrame(anim);
            } else {
                this.reticleGroup.visible = false;
            }
        };
        requestAnimationFrame(anim);
    }

    setupSelectionAura() {
        const THREE = this.THREE;
        this.selectionAura = new THREE.Group();
        this.selectionAura.name = 'FlightSelectionAura';
        this.selectionAura.visible = false;

        // 1. 发光全息基底圆环
        const ringGeo = new THREE.RingGeometry(18, 22, 36);
        ringGeo.rotateX(-Math.PI / 2);
        const ringMat = new THREE.MeshBasicMaterial({
            color: 0x38bdf8,
            side: THREE.DoubleSide,
            transparent: true,
            opacity: 0.85,
            depthTest: false
        });
        const ringMesh = new THREE.Mesh(ringGeo, ringMat);
        ringMesh.renderOrder = 999;
        this.selectionAura.add(ringMesh);

        // 2. 外部细发光环
        const outerGeo = new THREE.RingGeometry(25, 26, 36);
        outerGeo.rotateX(-Math.PI / 2);
        const outerMat = new THREE.MeshBasicMaterial({
            color: 0x06b6d4,
            side: THREE.DoubleSide,
            transparent: true,
            opacity: 0.5,
            depthTest: false
        });
        const outerMesh = new THREE.Mesh(outerGeo, outerMat);
        outerMesh.renderOrder = 999;
        this.selectionAura.add(outerMesh);

        // 3. 垂直全息扫描柱
        const beamGeo = new THREE.CylinderGeometry(20, 20, 32, 24, 1, true);
        const beamMat = new THREE.MeshBasicMaterial({
            color: 0x38bdf8,
            transparent: true,
            opacity: 0.16,
            side: THREE.DoubleSide,
            depthWrite: false
        });
        const beamMesh = new THREE.Mesh(beamGeo, beamMat);
        beamMesh.position.y = 16;
        this.selectionAura.add(beamMesh);

        this.scene.add(this.selectionAura);
    }

    setupCanvasInteractions() {
        const canvas = this.ctx.renderer.domElement;
        const THREE = this.THREE;
        const raycaster = new THREE.Raycaster();
        const mouse = new THREE.Vector2();

        // 鼠标悬停检测：滑过飞机时改变鼠标手势为 pointer
        canvas.addEventListener('pointermove', (e) => {
            const rect = canvas.getBoundingClientRect();
            mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
            mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
            raycaster.setFromCamera(mouse, this.camera);

            const planeHits = raycaster.intersectObjects(this.liveGroup.children, true);
            if (planeHits.length > 0) {
                let hitObj = planeHits[0].object;
                while (hitObj && hitObj !== this.liveGroup) {
                    if (hitObj.userData && hitObj.userData.flightId) {
                        canvas.style.cursor = 'pointer';
                        return;
                    }
                    hitObj = hitObj.parent;
                }
            }

            // 屏幕空间 40px 临近判断
            let near = false;
            const clickX = e.clientX - rect.left;
            const clickY = e.clientY - rect.top;
            for (const [id, item] of this.planesMap.entries()) {
                if (!item.plane || !item.plane.visible) continue;
                this.tempVec.copy(item.plane.position);
                this.tempVec.project(this.camera);
                if (this.tempVec.z > 1.0) continue;
                const px = ((this.tempVec.x + 1) / 2) * rect.width;
                const py = ((-this.tempVec.y + 1) / 2) * rect.height;
                if (Math.hypot(clickX - px, clickY - py) < 40) {
                    near = true;
                    break;
                }
            }
            canvas.style.cursor = near ? 'pointer' : 'default';
        });

        // 1. 双击场景任意位置（地面/跑道/建筑/草坪/飞机）：设为旋转中心
        canvas.addEventListener('dblclick', (e) => {
            e.preventDefault();
            const rect = canvas.getBoundingClientRect();
            mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
            mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;

            raycaster.setFromCamera(mouse, this.camera);

            // A. 检测是否双击在 3D 航班客机上
            const planeHits = raycaster.intersectObjects(this.liveGroup.children, true);
            let dblFlightId = null;
            if (planeHits.length > 0) {
                let hitObj = planeHits[0].object;
                while (hitObj && hitObj !== this.liveGroup) {
                    if (hitObj.userData && hitObj.userData.flightId) {
                        dblFlightId = hitObj.userData.flightId;
                        break;
                    }
                    hitObj = hitObj.parent;
                }
            }
            if (!dblFlightId) {
                let minDist = 45;
                const clickX = e.clientX - rect.left;
                const clickY = e.clientY - rect.top;
                for (const [id, item] of this.planesMap.entries()) {
                    if (!item.plane || !item.plane.visible) continue;
                    this.tempVec.copy(item.plane.position);
                    this.tempVec.project(this.camera);
                    if (this.tempVec.z > 1.0) continue;
                    const px = ((this.tempVec.x + 1) / 2) * rect.width;
                    const py = ((-this.tempVec.y + 1) / 2) * rect.height;
                    const d = Math.hypot(clickX - px, clickY - py);
                    if (d < minDist) {
                        minDist = d;
                        dblFlightId = id;
                    }
                }
            }
            if (dblFlightId) {
                this.selectFlight(dblFlightId);
                return;
            }

            // B. 检测是否双击在机场建筑物、跑道、停机坪、路网等几何体上
            const sceneHits = raycaster.intersectObjects(this.scene.children, true);
            const validHit = sceneHits.find(h => {
                if (!h.object || !h.object.visible) return false;
                if (h.object === this.liveGroup || h.object === this.reticleGroup) return false;
                if (h.object.type === 'Line' || h.object.type === 'Points') return false;
                return true;
            });

            if (validHit) {
                const pt = validHit.point;
                this.setOrbitCenter(pt, {
                    label: `场面坐标 (${Math.round(pt.x)}, ${Math.round(pt.z)})`,
                    targetDistance: 450
                });
                return;
            }

            // C. 兜底投影至机坪地表水平面 Y = 0.22
            const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -0.22);
            const groundPt = new THREE.Vector3();
            if (raycaster.ray.intersectPlane(groundPlane, groundPt)) {
                groundPt.x = Math.max(-1200, Math.min(1200, groundPt.x));
                groundPt.z = Math.max(-1500, Math.min(1500, groundPt.z));
                this.setOrbitCenter(groundPt, {
                    label: `地面 (${Math.round(groundPt.x)}, ${Math.round(groundPt.z)})`,
                    targetDistance: 500
                });
            }
        });

        // 2. 指针按下与中键/Alt键检测
        let downPos = { x: 0, y: 0 };
        canvas.addEventListener('pointerdown', (e) => {
            downPos = { x: e.clientX, y: e.clientY };

            // 鼠标中键按下 (button === 1) 或 Alt + 左键：快速设为旋转中心
            if (e.button === 1 || (e.button === 0 && e.altKey)) {
                e.preventDefault();
                const rect = canvas.getBoundingClientRect();
                mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
                mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
                raycaster.setFromCamera(mouse, this.camera);
                const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -0.22);
                const pt = new THREE.Vector3();
                if (raycaster.ray.intersectPlane(groundPlane, pt)) {
                    this.setOrbitCenter(pt, { label: `中键指定中心`, duration: 450 });
                }
            }

            // 用户开始拖拽平移或旋转时，若正处于持续跟随，自动解除跟随以保障用户控制权
            if (this.followingFlightId && (e.button === 2 || e.button === 0)) {
                this.followingFlightId = null;
                this.showToast('🔓 已解除航班跟随，恢复全自由操作');
                const followBtn = document.getElementById('btn-toggle-follow');
                if (followBtn) {
                    followBtn.classList.remove('active');
                    followBtn.textContent = '🔁 持续跟随伴飞';
                }
            }
        });

        // 3. 单击判定（未发生拖拽的纯点击）：点击飞机显示巡航状态与航班信息
        canvas.addEventListener('pointerup', (e) => {
            const dist = Math.hypot(e.clientX - downPos.x, e.clientY - downPos.y);
            if (dist > 5) return; // 移动距离超过5px视为拖拽，不触发点击

            if (e.button === 0 && !e.altKey) {
                const rect = canvas.getBoundingClientRect();
                mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
                mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
                raycaster.setFromCamera(mouse, this.camera);

                // A. 优先通过 3D 射线命中飞机实体（含 pickProxy 代理碰撞球）
                const planeHits = raycaster.intersectObjects(this.liveGroup.children, true);
                let hitFlightId = null;
                if (planeHits.length > 0) {
                    let hitObj = planeHits[0].object;
                    while (hitObj && hitObj !== this.liveGroup) {
                        if (hitObj.userData && hitObj.userData.flightId) {
                            hitFlightId = hitObj.userData.flightId;
                            break;
                        }
                        hitObj = hitObj.parent;
                    }
                }

                // B. 屏幕空间 45px 容错吸附拾取（确保远距离/小目标一键必中）
                if (!hitFlightId) {
                    let minDist = 45;
                    const clickX = e.clientX - rect.left;
                    const clickY = e.clientY - rect.top;
                    for (const [id, item] of this.planesMap.entries()) {
                        if (!item.plane || !item.plane.visible) continue;
                        this.tempVec.copy(item.plane.position);
                        this.tempVec.project(this.camera);
                        if (this.tempVec.z > 1.0) continue;
                        const px = ((this.tempVec.x + 1) / 2) * rect.width;
                        const py = ((-this.tempVec.y + 1) / 2) * rect.height;
                        const d = Math.hypot(clickX - px, clickY - py);
                        if (d < minDist) {
                            minDist = d;
                            hitFlightId = id;
                        }
                    }
                }

                if (hitFlightId) {
                    this.selectFlight(hitFlightId);
                }
            }
        });
    }

    setOrbitCenter(targetPos, options = {}) {
        const {
            duration = 750,
            keepCameraOffset = true,
            targetDistance = null,
            label = null,
            pulseReticle = true
        } = options;

        if (!this.controls || !this.camera) return;

        if (this._centerTween) {
            cancelAnimationFrame(this._centerTween);
            this._centerTween = null;
        }

        const startTgt = this.controls.target.clone();
        const endTgt = targetPos.clone();
        const startCam = this.camera.position.clone();

        let endCam = startCam.clone();
        if (keepCameraOffset) {
            const offset = endTgt.clone().sub(startTgt);
            endCam.add(offset);
        }

        const curDist = endCam.distanceTo(endTgt);
        if (targetDistance && curDist > targetDistance) {
            const dir = endCam.clone().sub(endTgt).normalize();
            endCam.copy(endTgt).add(dir.multiplyScalar(targetDistance));
        }

        this.animateCameraTo(startCam, endCam, startTgt, endTgt, duration);

        if (pulseReticle) {
            this.showPivotReticle(endTgt);
        }

        const name = label || `(${Math.round(endTgt.x)}, ${Math.round(endTgt.z)})`;
        this.updateCenterHUD(name);
        this.showToast(`🎯 旋转中心已切换至: ${name}`);
    }

    animateCameraTo(startCam, endCam, startTgt, endTgt, duration = 750) {
        if (this._centerTween) {
            cancelAnimationFrame(this._centerTween);
            this._centerTween = null;
        }

        const startTime = performance.now();
        const tick = (now) => {
            const elapsed = now - startTime;
            const progress = Math.min(elapsed / duration, 1.0);
            const ease = 1 - Math.pow(1 - progress, 3);

            this.controls.target.lerpVectors(startTgt, endTgt, ease);
            this.camera.position.lerpVectors(startCam, endCam, ease);
            this.controls.update();

            if (progress < 1.0) {
                this._centerTween = requestAnimationFrame(tick);
            } else {
                this._centerTween = null;
            }
        };
        this._centerTween = requestAnimationFrame(tick);
    }

    centerOnFlight(flightId) {
        this.selectedFlightId = flightId;
        const item = this.planesMap.get(flightId);
        if (!item) return;

        this.renderFlightCard(item.data);

        const p = item.plane.position;
        const isAir = item.data.status !== 'PARKED' && item.data.status !== 'TAXI_IN' && item.data.status !== 'TAXI_OUT' && item.data.status !== 'LANDED';
        const startTgt = this.controls.target.clone();
        const endTgt = new this.THREE.Vector3(p.x, p.y + (isAir ? 0 : 2), p.z);
        const startCam = this.camera.position.clone();

        const endCam = new this.THREE.Vector3(
            p.x + (p.x < 0 ? 50 : -50),
            p.y + (isAir ? 32 : 24),
            p.z + 60
        );

        this.animateCameraTo(startCam, endCam, startTgt, endTgt, 750);
        this.showPivotReticle(endTgt);
        const name = `✈️ ${item.data.flight_no} (${item.data.status})`;
        this.updateCenterHUD(name);
        this.showToast(`🎯 旋转中心: ${name} | 可围绕其 360° 自由旋转`);
    }

    toggleFollowFlight(flightId) {
        if (this.followingFlightId === flightId) {
            this.followingFlightId = null;
            this.showToast('🔓 已停止跟随，恢复自由漫游');
        } else {
            this.followingFlightId = flightId;
            this.centerOnFlight(flightId);
            this.showToast(`🔁 正在持续跟随客机飞行: ${flightId}`);
        }
        const followBtn = document.getElementById('btn-toggle-follow');
        if (followBtn) {
            followBtn.classList.toggle('active', !!this.followingFlightId);
            followBtn.textContent = this.followingFlightId ? '🔓 停止跟随' : '🔁 持续跟随';
        }
    }

    centerOnStand(standRef) {
        if (!this.ctx.standRegistry) return;
        const node = this.ctx.standRegistry.find(s => s.data.ref === standRef);
        if (!node) return;

        const st = node.data;
        const startTgt = this.controls.target.clone();
        const endTgt = new this.THREE.Vector3(st.px, 2.0, st.pz);
        const startCam = this.camera.position.clone();
        const endCam = new this.THREE.Vector3(st.px + 40, 26, st.pz + 45);

        this.animateCameraTo(startCam, endCam, startTgt, endTgt, 750);
        this.showPivotReticle(endTgt);
        const name = `🅿️ ${st.ref} 号机位`;
        this.updateCenterHUD(name);
        this.showToast(`🎯 旋转中心已切换至: ${name}`);

        if (window.inspectStand) {
            window.inspectStand(node);
        }
    }

    resetCenter() {
        const startTgt = this.controls.target.clone();
        const endTgt = new this.THREE.Vector3(0, 20, 0);
        const startCam = this.camera.position.clone();
        const endCam = new this.THREE.Vector3(880, 480, 560);

        this.followingFlightId = null;
        this.animateCameraTo(startCam, endCam, startTgt, endTgt, 800);
        this.showPivotReticle(endTgt);
        this.updateCenterHUD('全场中央');
        this.showToast('🌐 旋转中心已恢复至: 全场中央');
    }

    updateCenterHUD(name) {
        this.currentCenterName = name;
        const el = document.getElementById('hud-center-name');
        if (el) el.textContent = name;
    }

    showToast(msg) {
        let toast = document.getElementById('live-hud-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'live-hud-toast';
            toast.className = 'live-hud-toast';
            document.body.appendChild(toast);
        }
        toast.textContent = msg;
        toast.classList.remove('hidden');
        toast.classList.add('visible');
        if (this._toastTimer) clearTimeout(this._toastTimer);
        this._toastTimer = setTimeout(() => {
            toast.classList.remove('visible');
            setTimeout(() => toast.classList.add('hidden'), 300);
        }, 2200);
    }

    selectFlight(flightId) {
        this.selectedFlightId = flightId;
        const item = this.planesMap.get(flightId);
        if (!item) return;

        // 1. 弹出专业巡航状态与航班信息详情看板
        this.renderFlightCard(item.data);

        // 2. 将旋转中心对焦至该机（保持平滑运镜），并给出 Toast 反馈
        this.centerOnFlight(flightId);

        // 3. 激活 3D 选中全息光环
        if (this.selectionAura) {
            this.selectionAura.visible = true;
            this.selectionAura.position.copy(item.plane.position);
        }
    }

    flyToFlight(flightId) {
        this.selectFlight(flightId);
    }

    closeFlightCard() {
        this.selectedFlightId = null;
        const card = document.getElementById('live-flight-card');
        if (card) card.classList.add('hidden');
        if (this.selectionAura) this.selectionAura.visible = false;
        if (this.planesMap) {
            this.planesMap.forEach(item => {
                if (item.tagEl) item.tagEl.classList.remove('selected');
            });
        }
    }

    renderFlightCard(flight) {
        const card = document.getElementById('live-flight-card');
        if (!card) return;

        const info = AIRLINE_INFO[flight.airline] || AIRLINE_INFO.default;
        const acModel = AIRCRAFT_MODELS[flight.ac_type] || { name: `${flight.ac_type} 型客机`, desc: '民用喷气式支线/干线客机', cat: '干线客机' };
        const originPort = AIRPORT_NAMES[flight.origin] || { name: flight.origin, full: `${flight.origin} 机场`, city: flight.origin };
        const destPort = AIRPORT_NAMES[flight.destination] || { name: flight.destination, full: `${flight.destination} 机场`, city: flight.destination };

        const isAirborne = flight.status !== 'PARKED' && flight.status !== 'TAXI_IN' && flight.status !== 'TAXI_OUT' && flight.status !== 'LANDED';
        
        // 巡航高度与英制/公制转换
        const isGnd = flight.alt_baro === 'ground' || !flight.alt_baro || Number(flight.alt_baro) <= 0;
        const altFt = isGnd ? 0 : Math.round(Number(flight.alt_baro));
        const altM = Math.round(altFt * 0.3048);
        const altFtStr = isGnd ? '地面 (0)' : altFt.toLocaleString();
        const altMStr = isGnd ? '0' : altM.toLocaleString();
        const flLevel = isGnd ? 'GND' : (altFt >= 10000 ? `FL${Math.round(altFt / 100)}` : `A0${Math.round(altFt / 100)}`);

        // 巡航地速与马赫数
        const gsKts = Math.round(flight.gs || 0);
        const gsKmh = Math.round(gsKts * 1.852);
        const mach = (gsKmh / 1060).toFixed(2);

        // 升降速率 (Vertical Speed)
        const vsFpm = Math.round(flight.baro_rate || 0);
        const vsMs = (vsFpm * 0.00508).toFixed(1);
        let vsIcon = '➔';
        let vsColorClass = 'text-sky-300';
        let vsDesc = '平飞保持';
        if (vsFpm > 150) {
            vsIcon = '↗';
            vsColorClass = 'text-amber-400';
            vsDesc = `爬升中 (+${vsMs} m/s)`;
        } else if (vsFpm < -150) {
            vsIcon = '↘';
            vsColorClass = 'text-cyan-400';
            vsDesc = `进近下滑 (${vsMs} m/s)`;
        }

        // 真实航向方位 (True Track)
        const trackDeg = Math.round(flight.track || 0);
        const directions = ['正北 N', '东北 NNE', '东北 NE', '东东北 ENE', '正东 E', '东南 ESE', '东南 SE', '南东南 SSE', '正南 S', '西南 SSW', '西南 SW', '西西南 WSW', '正西 W', '西北 WNW', '西北 NW', '北西北 NNW'];
        const dirIdx = Math.round((trackDeg % 360) / 22.5) % 16;
        const trackDirText = directions[dirIdx];

        // 俯仰角 (Pitch Attitude)
        const pitchRad = flight.scene_pitch || 0;
        const pitchDeg = (pitchRad * (180 / Math.PI)).toFixed(1);
        let pitchDesc = '平飞姿态 (0°)';
        let pitchIcon = '✈️';
        if (Number(pitchDeg) > 0.5) {
            pitchDesc = `抬头爬升 (+${pitchDeg}°)`;
            pitchIcon = '🛫';
        } else if (Number(pitchDeg) < -0.5) {
            pitchDesc = `低头下滑 (${pitchDeg}°)`;
            pitchIcon = '🛬';
        }

        // 巡航状态全景分类与说明
        let statusBadge = '🟢 巡航平飞 (CRUISE)';
        let statusDesc = '处于巡航高度层平飞，航路管制雷达监控';
        let statusClass = 'status-cruise';
        let progressPercent = 65;
        let stageBadgeText = '巡航阶段';

        switch (flight.status) {
            case 'APPROACH':
                statusBadge = '🔵 五边进近下滑 (APPROACH)';
                statusDesc = '已建立五边盲降航道，沿 3° 下滑线稳定进近';
                statusClass = 'status-approach';
                progressPercent = 88;
                stageBadgeText = '五边进近';
                break;
            case 'CLIMB':
                statusBadge = '🟠 离港爬升 (CLIMB)';
                statusDesc = '起飞离港爬升中，按标准仪表离场程序 (SID) 爬升';
                statusClass = 'status-climb';
                progressPercent = 30;
                stageBadgeText = '离港爬升';
                break;
            case 'EN_ROUTE':
            case 'CRUISE':
                statusBadge = '🟢 巡航平飞 (CRUISE)';
                statusDesc = '处于巡航高度层平飞，航路巡航中';
                statusClass = 'status-cruise';
                progressPercent = 65;
                stageBadgeText = '巡航平飞';
                break;
            case 'LANDED':
            case 'ROLLOUT':
                statusBadge = '🛬 跑道滑跑 (ROLLOUT)';
                statusDesc = '已接地滑跑减速，正开启反推脱离跑道';
                statusClass = 'status-rollout';
                progressPercent = 95;
                stageBadgeText = '接地滑跑';
                break;
            case 'TAXI_IN':
                statusBadge = '🚖 入位滑行中 (TAXI IN)';
                statusDesc = '沿联络滑行道滑向指派登机机位';
                statusClass = 'status-taxi';
                progressPercent = 98;
                stageBadgeText = '滑行入位';
                break;
            case 'TAXI_OUT':
                statusBadge = '🚖 离位滑行中 (TAXI OUT)';
                statusDesc = '已推出，正滑向跑道起飞等待点';
                statusClass = 'status-taxi';
                progressPercent = 10;
                stageBadgeText = '滑行离位';
                break;
            case 'TAKEOFF':
                statusBadge = '🛫 跑道起飞加速 (TAKEOFF)';
                statusDesc = '全推力滑跑起飞，即将抬轮离地';
                statusClass = 'status-climb';
                progressPercent = 18;
                stageBadgeText = '起飞加速';
                break;
            case 'PARKED':
                statusBadge = '🅿️ 机位停泊靠桥 (PARKED)';
                statusDesc = '已停妥于指定机位，登机廊桥与地服对接';
                statusClass = 'status-parked';
                progressPercent = 100;
                stageBadgeText = '停机靠桥';
                break;
        }

        // 跑道与机位协同
        let runwayInfo = flight.direction === '进港' ? '04L 西跑道 (ILS CAT-II 盲降)' : '04R 东跑道 (离港专用跑道)';
        if (flight.scene_x > 0) runwayInfo = '04R 东跑道 (长3600m)';
        else if (flight.scene_x < 0) runwayInfo = '04L 西跑道 (长3600m)';

        let standInfo = flight.stand ? `T3 航站楼 ${flight.stand} 号机位 (双廊桥对接)` : (isAirborne ? '空中五边航道引导中' : '场面联络滑行道');

        card.classList.remove('hidden');
        card.innerHTML = `
            <div class="card-header">
                <div class="flex items-center gap-2.5">
                    <span class="airline-pill" style="background:${info.color};">${flight.airline}</span>
                    <div class="flex flex-col">
                        <div class="flex items-center gap-2">
                            <span class="font-extrabold text-lg text-white tracking-wide">${flight.flight_no}</span>
                            <span class="text-xs px-2 py-0.5 rounded-full font-mono bg-sky-950/80 text-sky-300 border border-sky-500/30">${flight.callsign}</span>
                        </div>
                        <span class="text-xs text-slate-300">${info.full || info.name} · <span class="font-mono text-slate-400">Hex: ${flight.hex}</span></span>
                    </div>
                </div>
                <button onclick="window.__LIVE_MODULE__.closeFlightCard()" class="card-close-btn" title="关闭面板">✕</button>
            </div>

            <!-- 执飞机型信息 -->
            <div class="card-aircraft-banner">
                <div class="flex items-center justify-between">
                    <span class="ac-title">✈️ ${acModel.name}</span>
                    <span class="ac-tag">${acModel.cat}</span>
                </div>
                <div class="ac-desc">${acModel.desc} · 3D 模型尺度 ${flight.ac_scale}x</div>
            </div>

            <!-- 航线与巡航进度条 -->
            <div class="card-route-card">
                <div class="route-airports">
                    <div class="route-port origin">
                        <div class="port-code">${flight.origin}</div>
                        <div class="port-name">${originPort.name}</div>
                        <div class="port-city">${originPort.city}</div>
                    </div>
                    <div class="route-vector">
                        <div class="vector-badge">${stageBadgeText}</div>
                        <div class="vector-track-bar">
                            <div class="track-fill" style="width: ${progressPercent}%;"></div>
                            <div class="track-plane" style="left: ${progressPercent}%;">✈</div>
                        </div>
                        <div class="vector-sub">距机场 ${flight.dist_km.toFixed(1)} km · ${flight.direction}</div>
                    </div>
                    <div class="route-port destination">
                        <div class="port-code">${flight.destination}</div>
                        <div class="port-name">${destPort.name}</div>
                        <div class="port-city">${destPort.city}</div>
                    </div>
                </div>
            </div>

            <!-- 实时巡航状态与飞行参数指标矩阵 -->
            <div class="card-section-title">
                <span>📊 实时巡航状态与飞行指标</span>
                <span class="pulse-indicator">● 实时遥测</span>
            </div>
            <div class="cruise-matrix">
                <!-- 巡航状态 -->
                <div class="matrix-cell col-span-2">
                    <span class="cell-label">巡航阶段与状态 (Flight Phase)</span>
                    <span class="cell-val ${statusClass}">${statusBadge}</span>
                    <span class="cell-sub">${statusDesc}</span>
                </div>
                <!-- 巡航高度 -->
                <div class="matrix-cell">
                    <span class="cell-label">巡航气压高度 (Altitude)</span>
                    <span class="cell-val text-amber-300 font-mono">${altFtStr} <span class="unit">ft</span></span>
                    <span class="cell-sub">${altMStr} m · 高度层 ${flLevel}</span>
                </div>
                <!-- 巡航地速 -->
                <div class="matrix-cell">
                    <span class="cell-label">巡航地速 (Ground Speed)</span>
                    <span class="cell-val text-cyan-300 font-mono">${gsKts} <span class="unit">kts</span></span>
                    <span class="cell-sub">${gsKmh} km/h · Mach ${mach}</span>
                </div>
                <!-- 升降速率 -->
                <div class="matrix-cell">
                    <span class="cell-label">升降速率 (Vertical Rate)</span>
                    <span class="cell-val ${vsColorClass} font-mono">${vsIcon} ${vsFpm > 0 ? '+' : ''}${vsFpm} <span class="unit">fpm</span></span>
                    <span class="cell-sub">${vsDesc}</span>
                </div>
                <!-- 巡航真航向 -->
                <div class="matrix-cell">
                    <span class="cell-label">真航向角 (Track Heading)</span>
                    <span class="cell-val text-sky-200 font-mono">${trackDeg}° <span class="unit">${trackDirText}</span></span>
                    <span class="cell-sub">磁偏角对齐 · 走廊真航向</span>
                </div>
                <!-- 俯仰姿态 -->
                <div class="matrix-cell col-span-2">
                    <span class="cell-label">姿态角与俯仰 (Pitch & Attitude)</span>
                    <div class="flex items-center justify-between">
                        <span class="cell-val text-emerald-300 font-mono">${pitchIcon} ${pitchDesc}</span>
                        <span class="text-xs text-slate-400 font-mono">偏航: ${(flight.scene_yaw || 0).toFixed(2)} rad</span>
                    </div>
                </div>
            </div>

            <!-- 场面协同与跑道/机位指派 -->
            <div class="card-acdm-panel">
                <div class="acdm-row">
                    <span class="acdm-k">🛣️ 跑道指派:</span>
                    <span class="acdm-v font-bold ${flight.direction === '进港' ? 'text-cyan-300' : 'text-amber-300'}">${runwayInfo}</span>
                </div>
                <div class="acdm-row">
                    <span class="acdm-k">🏢 机位指派:</span>
                    <span class="acdm-v font-bold ${flight.stand ? 'text-emerald-400' : 'text-slate-300'}">${standInfo}</span>
                </div>
                <div class="acdm-row">
                    <span class="acdm-k">🌐 三维坐标:</span>
                    <span class="acdm-v font-mono text-2xs text-slate-400">X: ${Math.round(flight.scene_x)} | Y: ${flight.scene_y.toFixed(1)} | Z: ${Math.round(flight.scene_z)}</span>
                </div>
            </div>

            <!-- 视点控制与交互按钮 -->
            <div class="card-footer">
                <div class="flex gap-2">
                    <button class="card-btn-track flex-1" onclick="window.__LIVE_MODULE__.centerOnFlight('${flight.id}')">
                        🎯 设为旋转中心
                    </button>
                    <button id="btn-toggle-follow" class="card-btn-follow ${this.followingFlightId === flight.id ? 'active' : ''}" onclick="window.__LIVE_MODULE__.toggleFollowFlight('${flight.id}')">
                        ${this.followingFlightId === flight.id ? '🔓 停止跟随' : '🔁 持续跟随伴飞'}
                    </button>
                </div>
                <button class="card-btn-reset w-full" onclick="window.__LIVE_MODULE__.resetCenter()">
                    🌐 恢复全场中心
                </button>
            </div>
        `;
    }

    injectHudStyles() {
        const style = document.createElement('style');
        style.textContent = `
            /* 飞常准 3D 雷达 HUD 样式表 */
            .live-radar-hud {
                position: fixed;
                top: 16px;
                right: 16px;
                z-index: 50;
                width: 330px;
                background: rgba(15, 23, 42, 0.82);
                backdrop-filter: blur(16px);
                -webkit-backdrop-filter: blur(16px);
                border: 1px solid rgba(56, 189, 248, 0.28);
                border-radius: 16px;
                box-shadow: 0 16px 36px rgba(0, 0, 0, 0.55), 0 0 20px rgba(56, 189, 248, 0.15);
                color: #e2e8f0;
                font-family: 'Noto Sans SC', system-ui, sans-serif;
                padding: 12px 14px;
                pointer-events: auto;
                user-select: none;
                transition: all 0.25s ease;
            }
            .hud-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                border-bottom: 1px solid rgba(51, 65, 85, 0.7);
                padding-bottom: 8px;
                margin-bottom: 10px;
            }
            .hud-brand {
                display: flex;
                align-items: center;
                gap: 8px;
            }
            .radar-dot {
                width: 8px;
                height: 8px;
                border-radius: 50%;
                background: #38bdf8;
                box-shadow: 0 0 10px #38bdf8;
                animation: pulse 1.8s infinite;
            }
            @keyframes pulse {
                0%, 100% { opacity: 1; transform: scale(1); }
                50% { opacity: 0.4; transform: scale(0.85); }
            }
            .hud-title {
                font-size: 13px;
                font-weight: 700;
                color: #f8fafc;
                letter-spacing: 0.5px;
            }
            .hud-status-badge {
                font-size: 10px;
                padding: 2px 6px;
                border-radius: 999px;
                font-weight: 600;
            }
            .hud-status-badge.emerald {
                background: rgba(16, 185, 129, 0.2);
                color: #34d399;
                border: 1px solid rgba(16, 185, 129, 0.4);
            }
            .hud-status-badge.amber {
                background: rgba(245, 158, 11, 0.2);
                color: #fbbf24;
                border: 1px solid rgba(245, 158, 11, 0.4);
            }
            .hud-btn-sm {
                background: rgba(51, 65, 85, 0.6);
                border: 1px solid rgba(100, 116, 139, 0.4);
                color: #cbd5e1;
                font-size: 11px;
                padding: 3px 8px;
                border-radius: 6px;
                cursor: pointer;
                transition: all 0.18s ease;
            }
            .hud-btn-sm:hover {
                background: rgba(56, 189, 248, 0.25);
                color: #fff;
                border-color: #38bdf8;
            }
            .hud-metrics {
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 6px;
                margin-bottom: 10px;
            }
            .metric-card {
                background: rgba(30, 41, 59, 0.7);
                border: 1px solid rgba(71, 85, 105, 0.4);
                border-radius: 8px;
                padding: 6px 4px;
                text-align: center;
            }
            .metric-val {
                font-size: 16px;
                font-weight: 700;
                color: #38bdf8;
                font-family: monospace;
            }
            .metric-lbl {
                font-size: 10px;
                color: #94a3b8;
                margin-top: 2px;
            }
            .hud-controls {
                display: flex;
                flex-direction: column;
                gap: 8px;
            }
            .control-row {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 6px;
            }
            .btn-group {
                display: flex;
                background: rgba(30, 41, 59, 0.8);
                border: 1px solid rgba(71, 85, 105, 0.4);
                border-radius: 8px;
                padding: 2px;
                flex: 1;
            }
            .mode-btn {
                flex: 1;
                font-size: 11px;
                font-weight: 600;
                padding: 5px 4px;
                border-radius: 6px;
                color: #94a3b8;
                background: transparent;
                border: none;
                cursor: pointer;
                transition: all 0.18s ease;
            }
            .mode-btn.active {
                background: #0284c7;
                color: #fff;
                box-shadow: 0 2px 8px rgba(2, 132, 199, 0.4);
            }
            .toggle-pill {
                font-size: 11px;
                padding: 5px 8px;
                border-radius: 8px;
                border: 1px solid rgba(56, 189, 248, 0.4);
                background: rgba(2, 132, 199, 0.2);
                color: #38bdf8;
                cursor: pointer;
                font-weight: 600;
            }
            .toggle-pill.active {
                background: rgba(16, 185, 129, 0.25);
                border-color: #10b981;
                color: #34d399;
            }
            .filter-group {
                display: flex;
                gap: 4px;
                width: 100%;
            }
            .filter-btn {
                flex: 1;
                font-size: 10px;
                font-weight: 600;
                padding: 4px;
                background: rgba(30, 41, 59, 0.7);
                border: 1px solid rgba(71, 85, 105, 0.4);
                border-radius: 6px;
                color: #94a3b8;
                cursor: pointer;
            }
            .filter-btn.active {
                background: rgba(56, 189, 248, 0.25);
                color: #38bdf8;
                border-color: #38bdf8;
            }
            .views-row {
                display: grid;
                grid-template-columns: repeat(5, 1fr);
                gap: 4px;
            }
            .hud-hint {
                font-size: 10px;
                color: #94a3b8;
                text-align: center;
                padding: 4px 6px;
                background: rgba(15, 23, 42, 0.5);
                border: 1px dashed rgba(71, 85, 105, 0.4);
                border-radius: 6px;
                letter-spacing: 0.2px;
            }
            .cam-btn {
                font-size: 9px;
                padding: 4px 2px;
                background: rgba(30, 41, 59, 0.6);
                border: 1px solid rgba(71, 85, 105, 0.4);
                border-radius: 6px;
                color: #cbd5e1;
                cursor: pointer;
            }
            .cam-btn:hover {
                background: rgba(56, 189, 248, 0.2);
                border-color: #38bdf8;
            }
            .bottom-row {
                border-top: 1px solid rgba(51, 65, 85, 0.7);
                padding-top: 8px;
            }
            .hud-btn-alt {
                font-size: 10px;
                padding: 4px 8px;
                background: rgba(220, 38, 38, 0.2);
                border: 1px solid rgba(220, 38, 38, 0.4);
                color: #f87171;
                border-radius: 6px;
                cursor: pointer;
            }
            .replay-bar {
                display: flex;
                align-items: center;
                gap: 4px;
            }
            .replay-btn {
                font-size: 10px;
                padding: 3px 6px;
                background: rgba(51, 65, 85, 0.6);
                border: 1px solid rgba(100, 116, 139, 0.4);
                color: #cbd5e1;
                border-radius: 4px;
                cursor: pointer;
            }
            .replay-btn.active {
                background: #dc2626;
                color: #fff;
                border-color: #dc2626;
                font-weight: 700;
            }
            .replay-time {
                font-size: 10px;
                color: #64748b;
                font-family: monospace;
            }

            /* 3D 标牌悬浮层与标签卡片 */
            .live-flight-tags {
                position: absolute;
                inset: 0;
                overflow: hidden;
                pointer-events: none;
                z-index: 25;
            }
            .live-flight-tag {
                position: absolute;
                pointer-events: auto;
                cursor: pointer;
                background: rgba(15, 23, 42, 0.88);
                backdrop-filter: blur(8px);
                -webkit-backdrop-filter: blur(8px);
                border: 1px solid rgba(56, 189, 248, 0.4);
                border-radius: 8px;
                box-shadow: 0 4px 14px rgba(0, 0, 0, 0.5);
                padding: 4px 8px;
                font-size: 10px;
                color: #f1f5f9;
                transform-origin: center bottom;
                margin-left: -50px;
                margin-top: -55px;
                transition: transform 0.08s linear, border-color 0.2s;
                white-space: nowrap;
            }
            .live-flight-tag:hover, .live-flight-tag.selected {
                border-color: #38bdf8;
                box-shadow: 0 0 16px rgba(56, 189, 248, 0.6);
                transform: scale(1.06);
            }
            .tag-header {
                display: flex;
                align-items: center;
                gap: 4px;
                font-weight: 700;
            }
            .tag-badge {
                font-size: 8px;
                padding: 1px 4px;
                border-radius: 3px;
                color: #fff;
            }
            .tag-no {
                color: #38bdf8;
                font-family: monospace;
            }
            .tag-dir {
                font-size: 9px;
                color: #94a3b8;
            }
            .tag-body {
                margin-top: 2px;
                font-size: 9px;
                color: #cbd5e1;
            }
            .tag-meta {
                color: #cbd5e1;
                font-weight: 500;
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 6px;
                margin-top: 2px;
            }
            .live-flight-tag.tag-air {
                border-color: rgba(56, 189, 248, 0.7);
                box-shadow: 0 4px 16px rgba(0, 0, 0, 0.6), 0 0 12px rgba(56, 189, 248, 0.35);
            }
            .live-flight-tag.tag-gnd {
                border-color: rgba(52, 211, 153, 0.5);
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
            }
            /* 飞行状态颜色标准：标牌专属外框与光晕 */
            .live-flight-tag.status-tag-approach {
                border-color: rgba(56, 189, 248, 0.9);
                box-shadow: 0 4px 18px rgba(0, 0, 0, 0.7), 0 0 16px rgba(56, 189, 248, 0.5);
            }
            .live-flight-tag.status-tag-climb {
                border-color: rgba(245, 158, 11, 0.9);
                box-shadow: 0 4px 18px rgba(0, 0, 0, 0.7), 0 0 16px rgba(245, 158, 11, 0.5);
            }
            .live-flight-tag.status-tag-cruise {
                border-color: rgba(16, 185, 129, 0.9);
                box-shadow: 0 4px 18px rgba(0, 0, 0, 0.7), 0 0 16px rgba(16, 185, 129, 0.5);
            }
            .live-flight-tag.status-tag-landed {
                border-color: rgba(52, 211, 153, 0.9);
                box-shadow: 0 4px 16px rgba(0, 0, 0, 0.7), 0 0 14px rgba(52, 211, 153, 0.45);
            }
            .live-flight-tag.status-tag-taxi {
                border-color: rgba(192, 132, 252, 0.9);
                box-shadow: 0 4px 16px rgba(0, 0, 0, 0.7), 0 0 14px rgba(192, 132, 252, 0.45);
            }
            .live-flight-tag.status-tag-parked {
                border-color: rgba(100, 116, 139, 0.75);
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.6);
            }
            .tag-status-pill {
                font-size: 8px;
                padding: 1px 5px;
                border-radius: 4px;
                border: 1px solid;
                font-weight: 700;
                display: inline-flex;
                align-items: center;
                gap: 2px;
            }
            /* 状态颜色标准图例栏样式 */
            .status-legend-row {
                background: rgba(15, 23, 42, 0.65);
                border: 1px solid rgba(71, 85, 105, 0.35);
                border-radius: 8px;
                padding: 6px 8px;
                display: flex;
                flex-direction: column;
                gap: 4px;
            }
            .legend-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
            }
            .legend-grid {
                display: flex;
                flex-wrap: wrap;
                gap: 4px;
            }
            .legend-chip {
                font-size: 9px;
                font-weight: 700;
                padding: 2px 6px;
                border-radius: 4px;
                display: flex;
                align-items: center;
                gap: 3px;
                cursor: default;
                transition: all 0.15s ease;
            }
            .legend-chip.approach {
                background: rgba(56, 189, 248, 0.18);
                border: 1px solid rgba(56, 189, 248, 0.55);
                color: #38bdf8;
            }
            .legend-chip.climb {
                background: rgba(245, 158, 11, 0.18);
                border: 1px solid rgba(245, 158, 11, 0.55);
                color: #fbbf24;
            }
            .legend-chip.cruise {
                background: rgba(16, 185, 129, 0.18);
                border: 1px solid rgba(16, 185, 129, 0.55);
                color: #34d399;
            }
            .legend-chip.taxi {
                background: rgba(192, 132, 252, 0.18);
                border: 1px solid rgba(192, 132, 252, 0.55);
                color: #c084fc;
            }
            .legend-chip.parked {
                background: rgba(100, 116, 139, 0.2);
                border: 1px solid rgba(100, 116, 139, 0.5);
                color: #94a3b8;
            }
            .legend-chip .dot {
                width: 5px;
                height: 5px;
                border-radius: 50%;
                background: currentColor;
                box-shadow: 0 0 5px currentColor;
            }
            .tag-alt-pill {
                font-size: 8px;
                padding: 1px 4px;
                border-radius: 4px;
                font-weight: 700;
                font-family: monospace;
            }
            .tag-alt-pill.air {
                background: rgba(245, 158, 11, 0.25);
                color: #fbbf24;
                border: 1px solid rgba(245, 158, 11, 0.5);
            }
            .tag-alt-pill.gnd {
                background: rgba(16, 185, 129, 0.2);
                color: #34d399;
                border: 1px solid rgba(16, 185, 129, 0.4);
            }
            .card-btn-reset {
                background: rgba(51, 65, 85, 0.8);
                border: 1px solid rgba(100, 116, 139, 0.5);
                color: #cbd5e1;
                font-size: 11px;
                padding: 6px 10px;
                border-radius: 8px;
                cursor: pointer;
            }
            .card-btn-reset:hover {
                background: rgba(56, 189, 248, 0.25);
                color: #fff;
            }

            /* 侧边栏航班清单 */
            .live-flight-drawer {
                position: fixed;
                top: 80px;
                left: 16px;
                width: 280px;
                max-height: calc(100vh - 120px);
                background: rgba(15, 23, 42, 0.88);
                backdrop-filter: blur(14px);
                border: 1px solid rgba(56, 189, 248, 0.3);
                border-radius: 14px;
                box-shadow: 0 16px 36px rgba(0, 0, 0, 0.5);
                z-index: 50;
                display: flex;
                flex-direction: column;
                pointer-events: auto;
                overflow: hidden;
            }
            .drawer-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 10px 14px;
                border-bottom: 1px solid rgba(51, 65, 85, 0.7);
            }
            .drawer-list {
                overflow-y: auto;
                padding: 8px;
                display: flex;
                flex-direction: column;
                gap: 6px;
            }
            .flight-list-item {
                background: rgba(30, 41, 59, 0.65);
                border: 1px solid rgba(71, 85, 105, 0.4);
                border-radius: 8px;
                padding: 8px 10px;
                cursor: pointer;
                transition: all 0.18s ease;
            }
            .flight-list-item:hover, .flight-list-item.selected {
                background: rgba(56, 189, 248, 0.2);
                border-color: #38bdf8;
            }

            /* 选中飞机详情卡片 (巡航状态与航班信息详情看板) */
            .live-flight-card {
                position: fixed;
                bottom: 24px;
                left: 24px;
                width: 410px;
                max-width: calc(100vw - 48px);
                background: rgba(15, 23, 42, 0.94);
                backdrop-filter: blur(20px);
                -webkit-backdrop-filter: blur(20px);
                border: 1px solid rgba(56, 189, 248, 0.45);
                border-radius: 16px;
                box-shadow: 0 24px 48px rgba(0, 0, 0, 0.75), 0 0 25px rgba(56, 189, 248, 0.2);
                z-index: 60;
                padding: 16px;
                pointer-events: auto;
                user-select: none;
                animation: flightCardSlideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1);
                color: #e2e8f0;
                font-family: 'Noto Sans SC', system-ui, sans-serif;
            }
            @keyframes flightCardSlideIn {
                from { opacity: 0; transform: translateY(20px) scale(0.97); }
                to { opacity: 1; transform: translateY(0) scale(1); }
            }
            .card-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                border-bottom: 1px solid rgba(51, 65, 85, 0.7);
                padding-bottom: 10px;
            }
            .card-close-btn {
                background: rgba(51, 65, 85, 0.5);
                border: 1px solid rgba(100, 116, 139, 0.4);
                color: #94a3b8;
                width: 26px;
                height: 26px;
                border-radius: 6px;
                display: flex;
                align-items: center;
                justify-content: center;
                cursor: pointer;
                transition: all 0.15s ease;
            }
            .card-close-btn:hover {
                background: rgba(239, 68, 68, 0.25);
                border-color: #ef4444;
                color: #fca5a5;
            }
            .airline-pill {
                font-size: 11px;
                font-weight: 800;
                padding: 3px 8px;
                border-radius: 6px;
                color: #fff;
                letter-spacing: 0.5px;
                box-shadow: 0 2px 6px rgba(0, 0, 0, 0.4);
            }
            .card-aircraft-banner {
                background: linear-gradient(135deg, rgba(30, 41, 59, 0.8), rgba(15, 23, 42, 0.7));
                border: 1px solid rgba(71, 85, 105, 0.4);
                border-radius: 8px;
                padding: 8px 12px;
                margin-top: 10px;
            }
            .ac-title {
                font-size: 13px;
                font-weight: 700;
                color: #f8fafc;
            }
            .ac-tag {
                font-size: 10px;
                font-weight: 600;
                background: rgba(2, 132, 199, 0.25);
                border: 1px solid rgba(56, 189, 248, 0.4);
                color: #38bdf8;
                padding: 1px 6px;
                border-radius: 4px;
            }
            .ac-desc {
                font-size: 11px;
                color: #94a3b8;
                margin-top: 2px;
            }
            .card-route-card {
                background: rgba(30, 41, 59, 0.65);
                border: 1px solid rgba(56, 189, 248, 0.2);
                border-radius: 10px;
                padding: 10px 14px;
                margin-top: 10px;
            }
            .route-airports {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
            }
            .route-port {
                display: flex;
                flex-direction: column;
                align-items: center;
                min-width: 65px;
            }
            .route-port.origin .port-code {
                color: #38bdf8;
            }
            .route-port.destination .port-code {
                color: #34d399;
            }
            .port-code {
                font-size: 20px;
                font-weight: 900;
                font-family: monospace;
                line-height: 1.1;
            }
            .port-name {
                font-size: 11px;
                font-weight: 700;
                color: #e2e8f0;
                margin-top: 2px;
            }
            .port-city {
                font-size: 10px;
                color: #94a3b8;
            }
            .route-vector {
                flex: 1;
                display: flex;
                flex-direction: column;
                align-items: center;
                padding: 0 8px;
            }
            .vector-badge {
                font-size: 10px;
                font-weight: 700;
                color: #7dd3fc;
                background: rgba(2, 132, 199, 0.3);
                border: 1px solid rgba(56, 189, 248, 0.35);
                border-radius: 999px;
                padding: 1px 8px;
                margin-bottom: 4px;
            }
            .vector-track-bar {
                width: 100%;
                height: 4px;
                background: rgba(51, 65, 85, 0.8);
                border-radius: 2px;
                position: relative;
                margin: 4px 0;
            }
            .track-fill {
                height: 100%;
                background: linear-gradient(90deg, #38bdf8, #34d399);
                border-radius: 2px;
            }
            .track-plane {
                position: absolute;
                top: 50%;
                transform: translate(-50%, -50%);
                font-size: 12px;
                color: #fff;
                text-shadow: 0 0 6px #38bdf8;
            }
            .vector-sub {
                font-size: 10px;
                color: #94a3b8;
                font-mono;
            }
            .card-section-title {
                display: flex;
                align-items: center;
                justify-content: space-between;
                font-size: 11px;
                font-weight: 700;
                color: #cbd5e1;
                margin: 12px 0 6px 0;
                letter-spacing: 0.3px;
            }
            .pulse-indicator {
                font-size: 10px;
                color: #34d399;
                font-weight: 600;
                animation: pulse 1.5s infinite;
            }
            .cruise-matrix {
                display: grid;
                grid-template-columns: repeat(2, 1fr);
                gap: 8px;
            }
            .col-span-2 {
                grid-column: span 2;
            }
            .matrix-cell {
                background: rgba(30, 41, 59, 0.55);
                border: 1px solid rgba(71, 85, 105, 0.35);
                border-radius: 8px;
                padding: 7px 10px;
                display: flex;
                flex-direction: column;
                transition: border-color 0.2s;
            }
            .matrix-cell:hover {
                border-color: rgba(56, 189, 248, 0.45);
            }
            .cell-label {
                font-size: 10px;
                color: #94a3b8;
            }
            .cell-val {
                font-size: 13px;
                font-weight: 800;
                margin-top: 2px;
            }
            .cell-val .unit {
                font-size: 10px;
                font-weight: 500;
                color: #94a3b8;
            }
            .cell-sub {
                font-size: 10px;
                color: #64748b;
                margin-top: 1px;
            }
            .status-approach {
                color: #38bdf8;
                text-shadow: 0 0 10px rgba(56, 189, 248, 0.5);
            }
            .status-climb {
                color: #fbbf24;
                text-shadow: 0 0 10px rgba(251, 191, 36, 0.5);
            }
            .status-cruise {
                color: #34d399;
                text-shadow: 0 0 10px rgba(52, 211, 153, 0.5);
            }
            .status-rollout {
                color: #a7f3d0;
            }
            .status-taxi {
                color: #c084fc;
            }
            .status-parked {
                color: #6ee7b7;
            }
            .card-acdm-panel {
                background: rgba(15, 23, 42, 0.6);
                border: 1px solid rgba(71, 85, 105, 0.3);
                border-radius: 8px;
                padding: 8px 10px;
                margin-top: 8px;
                display: flex;
                flex-direction: column;
                gap: 4px;
            }
            .acdm-row {
                display: flex;
                align-items: center;
                justify-content: space-between;
                font-size: 11px;
            }
            .acdm-k {
                color: #94a3b8;
            }
            .acdm-v {
                color: #f1f5f9;
            }
            .card-footer {
                margin-top: 12px;
                display: flex;
                flex-direction: column;
                gap: 6px;
            }
            .card-btn-track {
                background: linear-gradient(135deg, #0284c7, #0369a1);
                border: 1px solid #38bdf8;
                color: #fff;
                font-weight: 700;
                font-size: 11px;
                padding: 7px;
                border-radius: 8px;
                cursor: pointer;
                transition: all 0.15s ease;
            }
            .card-btn-track:hover {
                box-shadow: 0 0 14px rgba(56, 189, 248, 0.5);
            }
            .card-btn-follow {
                font-size: 11px;
                font-weight: 700;
                padding: 7px 12px;
                background: rgba(30, 41, 59, 0.85);
                border: 1px solid rgba(71, 85, 105, 0.5);
                border-radius: 8px;
                color: #cbd5e1;
                cursor: pointer;
                transition: all 0.18s ease;
            }
            .card-btn-follow.active {
                background: rgba(16, 185, 129, 0.3);
                border-color: #10b981;
                color: #34d399;
                box-shadow: 0 0 12px rgba(16, 185, 129, 0.35);
            }

            /* 旋转中心管理栏 */
            .center-row {
                display: flex;
                flex-direction: column;
                gap: 5px;
                background: rgba(15, 23, 42, 0.65);
                border: 1px solid rgba(56, 189, 248, 0.25);
                border-radius: 8px;
                padding: 6px 8px;
            }
            .center-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
            }
            .hud-center-pill {
                font-size: 10px;
                font-weight: bold;
                padding: 2px 8px;
                border-radius: 9999px;
                background: rgba(2, 132, 199, 0.35);
                border: 1px solid rgba(56, 189, 248, 0.5);
                color: #7dd3fc;
                max-width: 170px;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }
            .center-btn-group {
                display: grid;
                grid-template-columns: repeat(5, 1fr);
                gap: 3px;
            }
            .center-btn {
                font-size: 9px;
                font-weight: 600;
                padding: 4px 1px;
                background: rgba(30, 41, 59, 0.7);
                border: 1px solid rgba(71, 85, 105, 0.4);
                border-radius: 6px;
                color: #cbd5e1;
                cursor: pointer;
                transition: all 0.15s ease;
                white-space: nowrap;
                text-align: center;
            }
            .center-btn:hover {
                background: rgba(2, 132, 199, 0.3);
                border-color: #38bdf8;
                color: #fff;
            }
            .center-btn.active {
                background: #0284c7;
                border-color: #38bdf8;
                color: #fff;
                box-shadow: 0 1px 6px rgba(2, 132, 199, 0.4);
            }

            /* 视点切换提示 Toast */
            .live-hud-toast {
                position: fixed;
                bottom: 28px;
                left: 50%;
                transform: translateX(-50%) translateY(20px);
                z-index: 100;
                background: rgba(15, 23, 42, 0.92);
                backdrop-filter: blur(12px);
                border: 1px solid rgba(56, 189, 248, 0.6);
                border-radius: 30px;
                padding: 8px 20px;
                color: #f8fafc;
                font-size: 13px;
                font-weight: 600;
                box-shadow: 0 12px 28px rgba(0, 0, 0, 0.6), 0 0 16px rgba(56, 189, 248, 0.25);
                opacity: 0;
                pointer-events: none;
                transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
            }
            .live-hud-toast.visible {
                opacity: 1;
                transform: translateX(-50%) translateY(0);
            }
        `;
        document.head.appendChild(style);
    }
}

// 实例化并暴露全局钩子
window.__LIVE_MODULE__ = new LiveFlightsModule();
window.__LIVE_MODULE__.init();
