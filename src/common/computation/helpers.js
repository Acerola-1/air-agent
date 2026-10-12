// 固定业务规则 v1。未提供的新 PM 边界拒绝计算，避免编造阈值。
const pollutionRules = Object.freeze({
  CO: {daily:[2,4,14,24,36],hourly:[5,10,35,60,90],unit:"mg/m3"},
  NO2:{daily:[40,80,180,280,565],hourly:[100,200,700,1200,2340],unit:"ug/m3"},
  O3:{daily:[100,160,215,265,800],hourly:[160,200,300,400,800],"8hour":[100,160,215,265,800],unit:"ug/m3"},
  SO2:{daily:[50,150,475,800,1600],hourly:[150,500,650,800,800],unit:"ug/m3"},
  PM10:{old:[50,150,250,350,420],unit:"ug/m3"},
  "PM2.5":{old:[35,75,115,150,250],unit:"ug/m3"}
});
function pollutionLevel(factor,value,period,timestamp,unit) {
  factor=String(factor).toUpperCase();
  const rule=pollutionRules[factor];
  if(!rule || !Number.isFinite(value) || value<0) throw Error("因子或浓度无效");
  if(!["daily","hourly","8hour"].includes(period)) throw Error("统计时段无效");
  if(!/^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?$/.test(timestamp)) throw Error("必须提供本地完整日期／小时");
  if(period==="hourly" && timestamp.length<19) throw Error("小时记录必须提供小时");
  const stamp=timestamp.replace("T"," ");
  const year=Number(stamp.slice(0,4)), month=Number(stamp.slice(5,7)), day=Number(stamp.slice(8,10));
  if(year<100||month<1||month>12||day<1||day>new Date(Date.UTC(year,month,0)).getUTCDate()||(period==="hourly"&&Number(stamp.slice(11,13))>23)) throw Error("日期或小时无效");
  if(period==="hourly" && stamp.slice(14,19)!=="00:00") throw Error("小时记录应位于整点");
  const accepted=unit==="μg/m³"||unit==="µg/m³"||unit==="ug/m3" ? "ug/m3" : unit==="mg/m³"||unit==="mg/m3" ? "mg/m3" : "";
  if(accepted!==rule.unit) throw Error("浓度单位不匹配，不进行隐式转换");
  if(factor==="SO2" && period==="hourly" && value===800) throw Error("已知 SO2 小时规则在 800 处重叠，须先确认边界");
  let limits=rule[period];
  if(rule.old){
    const cut=period==="hourly" ? "2026-01-01 01:00:00" : "2026-01-01";
    if(stamp>=cut) throw Error("尚无已确认的新 PM 阈值，无法判定");
    if(factor==="PM2.5" && period!=="hourly") throw Error("尚无已确认的 PM2.5 日均阈值");
    if(period==="8hour") throw Error("PM 因子不支持八小时均值");
    limits=rule.old;
  }
  if(!limits) throw Error("因子不支持该统计时段");
  for(let i=0;i<limits.length;i++) if(value<=limits[i]) return i+1;
  return 6;
}
