// Fibona on iOS: the native shell.
// Runs core.js and app.js in one JavaScriptCore context on the main thread, and exposes one object, `native`,
// that reaches the Objective-C runtime by name, plus the keychain. Everything else is built in JavaScript.

#import <JavaScriptCore/JavaScriptCore.h>
#import <Security/Security.h>
#import <UIKit/UIKit.h>
#import <objc/runtime.h>

// --- VALUES --------------------------------------------------------------------------------------------
// JavaScriptCore converts NSString, NSNumber, NSArray and NSDictionary to JS values, and JS values back.
// Values in native memory are read and written by their Objective-C type encoding.

// Holds an object JavaScriptCore must not convert: an allocated but uninitialized NSString would be read as a
// string before init, and a block would become a JS function.
@interface FBBox : NSObject {
  @public
  id obj;
}
@end
@implementation FBBox
@end

static FBBox *box(id obj) {
  FBBox *b = [FBBox new];
  b->obj = obj;
  return b;
}

static id unbox(id v) {
  if ([v isKindOfClass:FBBox.class]) return ((FBBox *)v)->obj;
  return v == NSNull.null ? nil : v;
}

static const char *skipQualifiers(const char *t) {
  while (*t && strchr("rnNoORVA", *t)) t++;
  return t;
}

// Calls fn with each primitive field of a struct encoding and its offset, e.g. CGRect gives d@0 d@8 d@16 d@24.
static void eachField(const char *t, void (^fn)(const char *type, size_t offset)) {
  size_t offset = 0;
  for (const char *c = t; *c; c++) {
    if (*c == '{') {  // Skip the struct's name up to '='.
      while (*c && *c != '=') c++;
      continue;
    }
    if (*c == '}') continue;
    char type[2] = {*c, 0};
    NSUInteger size, align;
    NSGetSizeAndAlignment(type, &size, &align);
    offset = (offset + align - 1) / align * align;
    fn(type, offset);
    offset += size;
  }
}

static void put(const char *t, id v, char *p) {
  v = unbox(v);
  switch (*t) {
    case '@': case '#': *(void **)p = (__bridge void *)v; break;
    case ':': *(SEL *)p = v ? NSSelectorFromString(v) : NULL; break;
    case 'B': *(bool *)p = [v boolValue]; break;
    case 'c': *(char *)p = [v charValue]; break;
    case 'C': *(unsigned char *)p = [v unsignedCharValue]; break;
    case 's': *(short *)p = [v shortValue]; break;
    case 'S': *(unsigned short *)p = [v unsignedShortValue]; break;
    case 'i': *(int *)p = [v intValue]; break;
    case 'I': *(unsigned *)p = [v unsignedIntValue]; break;
    case 'l': case 'q': *(long long *)p = [v longLongValue]; break;
    case 'L': case 'Q': *(unsigned long long *)p = [v unsignedLongLongValue]; break;
    case 'f': *(float *)p = [v floatValue]; break;
    case 'd': *(double *)p = [v doubleValue]; break;
    case '{': {  // A flat array of numbers. ponytail: no arrays, unions or pointers inside structs.
      NSArray *values = v;
      __block NSUInteger i = 0;
      eachField(t, ^(const char *type, size_t offset) { put(type, values[i++], p + offset); });
      break;
    }
    case '^': case '*': *(void **)p = NULL; break;  // ponytail: pointers accept only null; add buffers when needed.
  }
}

static id get(const char *t, char *p) {
  switch (*t) {
    case '@': case '#': return (__bridge id) * (void **)p;
    case ':': return *(SEL *)p ? NSStringFromSelector(*(SEL *)p) : nil;
    case 'B': return @(*(bool *)p);
    case 'c': return @(*(char *)p);
    case 'C': return @(*(unsigned char *)p);
    case 's': return @(*(short *)p);
    case 'S': return @(*(unsigned short *)p);
    case 'i': return @(*(int *)p);
    case 'I': return @(*(unsigned *)p);
    case 'l': case 'q': return @(*(long long *)p);
    case 'L': case 'Q': return @(*(unsigned long long *)p);
    case 'f': return @(*(float *)p);
    case 'd': return @(*(double *)p);
    case '{': {
      NSMutableArray *values = [NSMutableArray array];
      eachField(t, ^(const char *type, size_t offset) { [values addObject:get(type, p + offset) ?: NSNull.null]; });
      return values;
    }
    case '^': case '*': return @((uintptr_t) * (void **)p);
    default: return nil;  // void, and encodings not handled here.
  }
}

// Cocoa's ownership rule: methods in these families return an object the caller owns, and init consumes its receiver.
static NSString *family(NSString *selector) {
  for (NSString *f in @[ @"alloc", @"new", @"copy", @"mutableCopy", @"init" ])
    if ([selector hasPrefix:f] && (selector.length == f.length || !islower([selector characterAtIndex:f.length])))
      return f;
  return nil;
}

static void throwJS(NSString *message) {
  JSContext.currentContext.exception = [JSValue valueWithNewErrorFromMessage:message inContext:JSContext.currentContext];
}

// --- CALLBACKS -----------------------------------------------------------------------------------------

static void callJS(JSValue *fn, NSArray *args) {
  if (NSThread.isMainThread) [fn callWithArguments:args];
  else dispatch_async(dispatch_get_main_queue(), ^{ [fn callWithArguments:args]; });
}

static const char *protocolTypes(Protocol *p, SEL s) {
  for (int required = 1; required >= 0; required--) {
    struct objc_method_description d = protocol_getMethodDescription(p, s, required, YES);
    if (d.types) return d.types;
  }
  unsigned n;
  Protocol *__unsafe_unretained *parents = protocol_copyProtocolList(p, &n);
  const char *types = NULL;
  for (unsigned i = 0; i < n && !types; i++) types = protocolTypes(parents[i], s);
  free(parents);
  return types;
}

// An object whose methods are JS functions: a delegate, data source, action target or timer target.
@interface FBDelegate : NSObject {
  @public
  JSValue *handlers;  // Keys are selectors with '_' for ':'.
  NSArray<NSString *> *protocols;  // Where argument and return types come from.
}
@end

@implementation FBDelegate

static NSString *jsName(SEL s) {
  return [NSStringFromSelector(s) stringByReplacingOccurrencesOfString:@":" withString:@"_"];
}

- (BOOL)respondsToSelector:(SEL)s {
  return [super respondsToSelector:s] || [handlers hasProperty:jsName(s)];
}

- (NSMethodSignature *)methodSignatureForSelector:(SEL)s {
  NSMethodSignature *sig = [super methodSignatureForSelector:s];
  if (sig || ![handlers hasProperty:jsName(s)]) return sig;
  for (NSString *name in protocols) {
    Protocol *p = objc_getProtocol(name.UTF8String);
    const char *types = p ? protocolTypes(p, s) : NULL;
    if (types) return [NSMethodSignature signatureWithObjCTypes:types];
  }
  // Not in a protocol: an action or timer selector, whose arguments are all objects.
  NSUInteger colons = [NSStringFromSelector(s) componentsSeparatedByString:@":"].count - 1;
  NSString *types = [@"v@:" stringByPaddingToLength:3 + colons withString:@"@" startingAtIndex:0];
  return [NSMethodSignature signatureWithObjCTypes:types.UTF8String];
}

- (void)forwardInvocation:(NSInvocation *)inv {
  if (!NSThread.isMainThread) {
    // ponytail: deadlocks if the main thread is waiting on this thread; queue it if an API ever needs that.
    dispatch_sync(dispatch_get_main_queue(), ^{ [self forwardInvocation:inv]; });
    return;
  }
  NSMethodSignature *sig = inv.methodSignature;
  NSMutableArray *args = [NSMutableArray array];
  for (NSUInteger i = 2; i < sig.numberOfArguments; i++) {
    const char *t = skipQualifiers([sig getArgumentTypeAtIndex:i]);
    NSUInteger size;
    NSGetSizeAndAlignment(t, &size, NULL);
    NSMutableData *buf = [NSMutableData dataWithLength:size];
    [inv getArgument:buf.mutableBytes atIndex:i];
    [args addObject:get(t, buf.mutableBytes) ?: NSNull.null];
  }
  JSValue *result = [handlers invokeMethod:jsName(inv.selector) withArguments:args];
  const char *rt = skipQualifiers(sig.methodReturnType);
  if (*rt == 'v') return;
  id value = result.isUndefined || result.isNull ? nil : result.toObject;
  if (*rt == '@' && value) CFAutorelease(CFBridgingRetain(value));  // Outlive this method, for the caller.
  NSMutableData *buf = [NSMutableData dataWithLength:sig.methodReturnLength];
  put(rt, value, buf.mutableBytes);
  [inv setReturnValue:buf.mutableBytes];
}

@end

// --- NATIVE --------------------------------------------------------------------------------------------

@protocol FBNativeExports <JSExport>
- (id)cls:(NSString *)name;
JSExportAs(send, -(id)send:(id)target selector:(NSString *)name args:(NSArray *)args);
JSExportAs(block, -(id)block:(NSString *)types fn:(JSValue *)fn);
JSExportAs(delegate, -(id)delegate:(JSValue *)handlers protocols:(NSArray *)protocols);
- (void)log:(NSString *)text;
- (NSString *)keychainGet:(NSString *)account;
JSExportAs(keychainSet, -(int)keychainSet:(NSString *)account value:(NSString *)value);
@end

@interface FBNative : NSObject <FBNativeExports>
@end

@implementation FBNative

- (id)cls:(NSString *)name {
  return NSClassFromString(name);
}

// Sends a message with arguments converted by the method's type encoding, e.g. send(label, "setText:", ["hi"]).
- (id)send:(id)target selector:(NSString *)name args:(NSArray *)args {
  target = unbox(target);
  SEL sel = NSSelectorFromString(name);
  @try {
    NSMethodSignature *sig = [target methodSignatureForSelector:sel];
    if (!sig) return throwJS([NSString stringWithFormat:@"%@ does not respond to %@", target, name]), nil;
    if (args.count != sig.numberOfArguments - 2)
      return throwJS([NSString stringWithFormat:@"%@ takes %lu arguments, got %lu", name,
                                                (unsigned long)sig.numberOfArguments - 2, (unsigned long)args.count]), nil;
    NSInvocation *inv = [NSInvocation invocationWithMethodSignature:sig];
    inv.target = target;
    inv.selector = sel;
    for (NSUInteger i = 0; i < args.count; i++) {
      const char *t = skipQualifiers([sig getArgumentTypeAtIndex:i + 2]);
      NSUInteger size;
      NSGetSizeAndAlignment(t, &size, NULL);
      NSMutableData *buf = [NSMutableData dataWithLength:size];
      put(t, args[i], buf.mutableBytes);
      [inv setArgument:buf.mutableBytes atIndex:i + 2];
    }
    NSString *owned = family(name);
    if ([owned isEqual:@"init"]) (void)CFBridgingRetain(target);  // init consumes the reference JS still holds.
    [inv invoke];
    const char *rt = skipQualifiers(sig.methodReturnType);
    if (*rt == 'v') return nil;
    NSMutableData *ret = [NSMutableData dataWithLength:sig.methodReturnLength];
    [inv getReturnValue:ret.mutableBytes];
    if (*rt != '@' || !owned) return get(rt, ret.mutableBytes);
    id obj = CFBridgingRelease(*(CFTypeRef *)ret.mutableBytes);  // Take ownership of the +1 result.
    return [owned isEqual:@"alloc"] ? box(obj) : obj;
  } @catch (NSException *e) {
    return throwJS([NSString stringWithFormat:@"%@: %@", e.name, e.reason]), nil;
  }
}

// A block that calls fn on the main thread. Blocks need native code for each signature, so only these exist.
- (id)block:(NSString *)types fn:(JSValue *)fn {
#define N(x) ((x) ?: NSNull.null)
  id block = nil;  // Assigning to a strong variable copies the block to the heap.
  if ([types isEqual:@"v"]) block = ^{ callJS(fn, @[]); };
  if ([types isEqual:@"v@"]) block = ^(id a) { callJS(fn, @[ N(a) ]); };
  if ([types isEqual:@"v@@"]) block = ^(id a, id b) { callJS(fn, @[ N(a), N(b) ]); };
  if ([types isEqual:@"v@@@"]) block = ^(id a, id b, id c) { callJS(fn, @[ N(a), N(b), N(c) ]); };
  if ([types isEqual:@"vB"]) block = ^(BOOL a) { callJS(fn, @[ @(a) ]); };
  if ([types isEqual:@"vB@"]) block = ^(BOOL a, id b) { callJS(fn, @[ @(a), N(b) ]); };
#undef N
  if (!block) return throwJS([@"Unsupported block types: " stringByAppendingString:types]), nil;
  return box(block);
}

- (id)delegate:(JSValue *)handlers protocols:(NSArray *)protocols {
  FBDelegate *d = [FBDelegate new];
  d->handlers = handlers;
  d->protocols = protocols;
  return d;
}

// Secrets such as the model settings, as generic passwords readable only while the phone is unlocked and never
// synced to other devices. Native because the keychain API is C functions, which the bridge cannot call.
// The simulator's keychain needs an entitlement its ad-hoc build cannot carry, so there they go to user defaults.
#if TARGET_OS_SIMULATOR
static NSString *defaultsKey(NSString *account) {
  return [@"fibona.keychain." stringByAppendingString:account];
}

- (NSString *)keychainGet:(NSString *)account {
  return [NSUserDefaults.standardUserDefaults stringForKey:defaultsKey(account)];
}

- (int)keychainSet:(NSString *)account value:(NSString *)value {
  [NSUserDefaults.standardUserDefaults setObject:value forKey:defaultsKey(account)];  // nil removes it.
  return errSecSuccess;
}
#else
static NSDictionary *keychainItem(NSString *account) {
  return @{(__bridge id)kSecClass : (__bridge id)kSecClassGenericPassword,
           (__bridge id)kSecAttrService : NSBundle.mainBundle.bundleIdentifier,
           (__bridge id)kSecAttrAccount : account};
}

- (NSString *)keychainGet:(NSString *)account {
  NSMutableDictionary *query = [keychainItem(account) mutableCopy];
  query[(__bridge id)kSecReturnData] = @YES;
  CFTypeRef data = NULL;
  if (SecItemCopyMatching((__bridge CFDictionaryRef)query, &data) != errSecSuccess) return nil;
  return [[NSString alloc] initWithData:CFBridgingRelease(data) encoding:NSUTF8StringEncoding];
}

// Replaces the item, or deletes it when value is null. Returns the OSStatus: 0 on success.
- (int)keychainSet:(NSString *)account value:(NSString *)value {
  SecItemDelete((__bridge CFDictionaryRef)keychainItem(account));
  if (!value) return errSecSuccess;
  NSMutableDictionary *item = [keychainItem(account) mutableCopy];
  item[(__bridge id)kSecValueData] = [value dataUsingEncoding:NSUTF8StringEncoding];
  item[(__bridge id)kSecAttrAccessible] = (__bridge id)kSecAttrAccessibleWhenUnlockedThisDeviceOnly;
  return SecItemAdd((__bridge CFDictionaryRef)item, NULL);
}
#endif

- (void)log:(NSString *)text {
  fputs(text.UTF8String, stderr);
}

@end

// --- APP -----------------------------------------------------------------------------------------------

@interface FBApp : UIResponder <UIApplicationDelegate>
@property JSContext *context;
@end

@implementation FBApp

- (BOOL)application:(UIApplication *)app didFinishLaunchingWithOptions:(NSDictionary *)options {
  self.context = [JSContext new];
  self.context.exceptionHandler = ^(JSContext *c, JSValue *e) {
    fprintf(stderr, "[fibona] uncaught: %s\n%s\n", e.toString.UTF8String, e[@"stack"].toString.UTF8String);
  };
  self.context[@"native"] = [FBNative new];
  for (NSString *name in @[ @"core", @"app" ]) {
    NSString *path = [NSBundle.mainBundle pathForResource:name ofType:@"js"];
    NSString *source = [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil];
    if (source) [self.context evaluateScript:source withSourceURL:[NSURL fileURLWithPath:path]];
  }
  return YES;
}

// A URL that opens the app, such as a widget region's (fibona://widget/<name>), goes to JavaScript's openURL.
- (BOOL)application:(UIApplication *)app openURL:(NSURL *)url options:(NSDictionary *)options {
  [self.context[@"openURL"] callWithArguments:@[ url.absoluteString ]];
  return YES;
}

@end

int main(int argc, char *argv[]) {
  @autoreleasepool {
    return UIApplicationMain(argc, argv, nil, @"FBApp");
  }
}
