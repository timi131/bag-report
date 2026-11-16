// Автобаг REDSMS для Postman и Newman.
// Хранится в переменной redsms_lib; подключается в тестах запроса строкой
//   eval(pm.variables.get('redsms_lib'));
// Проверки пишутся через redsms.check(...) вместо pm.test(...), в конце — redsms.report().
// Если хоть одна проверка упала, в трекер уходит баг-репорт. Повторный прогон того же
// падения не плодит дубликаты, пока баг открыт (статус «Новый» или «В работе»).
var redsms = (function () {
  var failures = [];

  function check(name, fn) {
    pm.test(name, function () {
      try { fn(); }
      catch (e) { failures.push({ name: name, error: String((e && e.message) || e) }); throw e; }
    });
  }

  function b64(s) {
    if (typeof btoa === 'function') return btoa(unescape(encodeURIComponent(s)));
    return CryptoJS.enc.Base64.stringify(CryptoJS.enc.Utf8.parse(s));
  }

  function report(opts) {
    opts = opts || {};
    if (!failures.length) return;
    var base = String(pm.variables.get('redsms_url') || '').replace(/\/+$/, '');
    if (!base) { console.warn('REDSMS: не задана переменная redsms_url — баг не отправлен'); return; }

    var res = pm.response;
    var body = '';
    try { body = res.text(); } catch (e) { body = ''; }
    if (body.length > 1500) body = body.slice(0, 1500) + '\n…';
    var envName = pm.environment && pm.environment.name ? ', окружение «' + pm.environment.name + '»' : '';

    var bug = {
      title: ('[Postman] ' + pm.info.requestName + ': ' + failures.map(function (f) { return f.name; }).join('; ')).slice(0, 200),
      severity: opts.severity || (res.code >= 500 ? 'critical' : 'major'),
      priority: opts.priority || 'medium',
      env: {
        version: pm.variables.get('redsms_version') || '',
        platform: 'Postman' + envName,
        stand: pm.variables.get('redsms_stand') || 'test',
        account: opts.account || ''
      },
      steps: [
        'Открыть в Postman запрос «' + pm.info.requestName + '»',
        'Отправить ' + pm.request.method + ' ' + pm.request.url.toString(),
        'Проверить ответ'
      ],
      expected: failures.map(function (f) { return '• ' + f.name; }).join('\n'),
      actual: 'HTTP ' + res.code + ' ' + res.status + ', ' + res.responseTime + ' мс\n\n' +
        failures.map(function (f) { return f.name + ': ' + f.error; }).join('\n') +
        (body ? '\n\nОтвет сервера:\n' + body : ''),
      repro: 'always',
      source: 'postman',
      dedupe: true
    };

    var header = [{ key: 'Content-Type', value: 'application/json' }];
    var user = pm.variables.get('redsms_user') || '';
    var password = pm.variables.get('redsms_password') || '';
    if (password) header.push({ key: 'Authorization', value: 'Basic ' + b64(user + ':' + password) });

    pm.sendRequest({
      url: base + '/api/bugs',
      method: 'POST',
      header: header,
      body: { mode: 'raw', raw: JSON.stringify(bug) }
    }, function (err, r) {
      if (err) { console.error('REDSMS: баг не отправлен — ' + err); return; }
      if (r.code >= 300) { console.error('REDSMS: сервер ответил ' + r.code + ' — ' + r.text()); return; }
      var saved = r.json();
      var short = 'BUG-' + saved.id.slice(-5).toUpperCase();
      console.log(saved.duplicate ? 'REDSMS: такой баг уже открыт — ' + short : 'REDSMS: заведён ' + short);
    });
  }

  return { check: check, report: report, failures: failures };
})();
